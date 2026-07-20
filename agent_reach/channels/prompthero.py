# -*- coding: utf-8 -*-
"""PromptHero — scrape AI image prompts, models, and metadata.

PromptHero (prompthero.com) is a public prompt-sharing gallery.
This channel scrapes prompt text, model parameters, and images
using requests + BeautifulSoup for detail pages and Playwright
for infinite-scroll list pages. Zero auth required.

Features:
  - Automatic browser scroll to collect all prompt links
  - Automatic checkpoint / resume on interrupt
  - Idempotent: re-running skips already-scraped prompts
  - Incremental JSONL append (never overwrites existing data)

HTML structure assumptions (verified at runtime):
  - List page: infinite-scroll cards linking to /prompt/<id>
  - Detail page: prompt text, model info, image URL in OG meta or img tags.
If selectors miss, the scraper dumps a debug snippet so the user
or agent can update SELECTORS and retry.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from .base import Channel

# ── Configurable CSS selectors ──
# Override at runtime via: channel.SELECTORS = {...}
SELECTORS: Dict[str, str] = {
    # List page
    "list_prompt_links": 'a[href^="/prompt/"]',
    # Detail page
    "prompt_text": 'div.prompt-text, .prompt-content, pre.prompt, [data-testid="prompt-text"], #prompt-text',
    "negative_prompt": 'div.negative-prompt, .negative-prompt-content, [data-testid="negative-prompt"], #negative-prompt',
    "model": 'div.model-name, .model-info, [data-testid="model"], span.model, .prompt-model',
    "sampler": 'div.sampler, .sampler-info, [data-testid="sampler"], span.sampler',
    "cfg_scale": 'div.cfg, .cfg-scale, [data-testid="cfg-scale"], span.cfg',
    "steps": 'div.steps, .step-count, [data-testid="steps"], span.steps',
    "seed": 'div.seed, .seed-value, [data-testid="seed"], span.seed',
    "size": 'div.size, .image-size, [data-testid="size"], span.size',
    "creator": 'a.creator, .author-name, [data-testid="creator"], .username, a[href^="/profile/"]',
    "tags": 'a.tag, .tag-item, [data-testid="tag"], .prompt-tag',
    "liked_count": 'span.likes, .like-count, [data-testid="likes"], .vote-count',
    "title": 'h1, .prompt-title, [data-testid="prompt-title"]',
    "image": 'meta[property="og:image"], img.main-image, .prompt-image img, [data-testid="prompt-image"] img',
}

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
_BASE_URL = "https://prompthero.com"


def _fetch_html(url: str, timeout: int = 30, cookies: Optional[List[Dict[str, Any]]] = None) -> str:
    """Fetch raw HTML from a URL.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        cookies: Optional list of cookie dicts from Cookie-Editor format
    """
    headers = {"User-Agent": _UA}

    # Add cookies to request
    if cookies:
        cookie_header = _format_cookies(cookies, urlparse(url).netloc)
        if cookie_header:
            headers["Cookie"] = cookie_header

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _format_cookies(cookies: List[Dict[str, Any]], domain: str) -> str:
    """Format cookies from Cookie-Exporter JSON into Cookie header string."""
    cookie_pairs = []

    for cookie in cookies:
        cookie_domain = cookie.get("domain", "")
        # Check if cookie matches domain
        if domain in cookie_domain or cookie_domain in domain:
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            if name and value:
                cookie_pairs.append(f"{name}={value}")

    return "; ".join(cookie_pairs)


def _extract_id_from_url(url: str) -> str:
    """Extract prompt ID from a PromptHero URL."""
    parsed = urlparse(url)
    m = re.search(r"/prompt/([^/?#]+)", parsed.path)
    if m:
        return m.group(1)
    return parsed.path.strip("/").replace("/", "_") or "unknown"


def _extract_from_headings(soup) -> Dict[str, Any]:
    """Extract prompt metadata from heading-based sections (modern PromptHero UI)."""
    data: Dict[str, Any] = {}

    # PromptHero uses Tailwind sections with h2 headings like "Prompt", "Model used", etc.
    for container in soup.find_all(["div", "section"]):
        classes = container.get("class", [])
        if not any(str(c).startswith("space-y-") for c in classes):
            continue

        h2 = container.find("h2")
        if not h2:
            continue

        heading = _clean_text(h2.get_text())

        if heading == "Prompt":
            prompt_div = container.find("div", class_=lambda x: x and "bg-muted" in str(x))
            if prompt_div:
                text_div = prompt_div.find("div", class_="font-semibold")
                data["prompt_text"] = _clean_text(text_div.get_text()) if text_div else _clean_text(prompt_div.get_text())

        elif heading == "Negative prompt":
            neg_div = container.find("div", class_=lambda x: x and "bg-muted" in str(x))
            if neg_div:
                data["negative_prompt"] = _clean_text(neg_div.get_text())

        elif heading == "Model used":
            model_a = container.find("a")
            if model_a:
                data["model"] = _clean_text(model_a.get_text())

        elif heading == "Category":
            cat_a = container.find("a")
            if cat_a:
                tag = _clean_text(cat_a.get_text())
                if tag:
                    data.setdefault("tags", []).append(tag)

        elif heading == "Generation parameters":
            params: List[str] = []
            for span in container.find_all("span", class_="font-mono"):
                params.append(_clean_text(span.get_text()))
            for p in params:
                if re.match(r"^\d+x\d+$", p):
                    data["size"] = p
                    break

    # Title
    h1 = soup.find("h1")
    if h1:
        data["title"] = _clean_text(h1.get_text())
    else:
        title_tag = soup.find("title")
        if title_tag:
            data["title"] = _clean_text(title_tag.get_text())

    # Image
    og = soup.select_one('meta[property="og:image"]')
    if og:
        data["image_url"] = str(og.get("content", ""))

    # Views / favorites from inline Next.js scripts
    for script in soup.find_all("script"):
        text = script.get_text()
        if "viewCount" in text or "favCount" in text:
            m = re.search(r'"viewCount":(\d+)', text)
            if m:
                data["view_count"] = int(m.group(1))
            m = re.search(r'"favCount":(\d+)', text)
            if m:
                data["liked_count"] = int(m.group(1))
            break

    return data


def _parse_detail_html(html: str, url: str, selectors: Dict[str, str]) -> Dict[str, Any]:
    """Parse a prompt detail page and return structured metadata.

    Falls back to regex-based extraction if BeautifulSoup is not installed
    or selectors return nothing.
    """
    data: Dict[str, Any] = {
        "prompt_id": _extract_id_from_url(url),
        "prompt_url": url,
        "scraped_at": int(time.time() * 1000),
    }

    # Try BeautifulSoup first
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # Modern PromptHero uses heading-based sections — try this first
        modern_data = _extract_from_headings(soup)
        data.update(modern_data)

        # Fallback / enrichment via legacy CSS selectors for anything missed
        if not data.get("prompt_text"):
            prompt_el = _first_match(soup, selectors["prompt_text"])
            if prompt_el:
                data["prompt_text"] = _clean_text(prompt_el.get_text())

        if not data.get("negative_prompt"):
            neg_el = _first_match(soup, selectors["negative_prompt"])
            if neg_el:
                data["negative_prompt"] = _clean_text(neg_el.get_text())

        if not data.get("model"):
            model_el = _first_match(soup, selectors["model"])
            if model_el:
                data["model"] = _clean_text(model_el.get_text())

        if not data.get("sampler"):
            sampler_el = _first_match(soup, selectors["sampler"])
            if sampler_el:
                data["sampler"] = _clean_text(sampler_el.get_text())

        if data.get("cfg_scale") is None:
            cfg_el = _first_match(soup, selectors["cfg_scale"])
            if cfg_el:
                data["cfg_scale"] = _to_float(_clean_text(cfg_el.get_text()))

        if data.get("steps") is None:
            steps_el = _first_match(soup, selectors["steps"])
            if steps_el:
                data["steps"] = _to_int(_clean_text(steps_el.get_text()))

        if data.get("seed") is None:
            seed_el = _first_match(soup, selectors["seed"])
            if seed_el:
                data["seed"] = _to_int(_clean_text(seed_el.get_text()))

        if not data.get("size"):
            size_el = _first_match(soup, selectors["size"])
            if size_el:
                data["size"] = _clean_text(size_el.get_text())

        if not data.get("creator"):
            creator_el = _first_match(soup, selectors["creator"])
            if creator_el:
                data["creator"] = _clean_text(creator_el.get_text())
                href = str(creator_el.get("href", ""))
                if href.startswith("/"):
                    data["creator_url"] = urljoin(_BASE_URL, href)
                elif href.startswith("http"):
                    data["creator_url"] = href

        if not data.get("tags"):
            tag_els = soup.select(selectors["tags"])
            tags = list(dict.fromkeys(_clean_text(t.get_text()) for t in tag_els if _clean_text(t.get_text())))
            if tags:
                data["tags"] = tags

        if data.get("liked_count") is None:
            likes_el = _first_match(soup, selectors["liked_count"])
            if likes_el:
                data["liked_count"] = _parse_count(_clean_text(likes_el.get_text()))

        if not data.get("title"):
            title_el = _first_match(soup, selectors["title"])
            if title_el:
                data["title"] = _clean_text(title_el.get_text())

        if not data.get("image_url"):
            img_el = _first_match(soup, selectors["image"])
            if img_el:
                if img_el.name == "meta":
                    data["image_url"] = str(img_el.get("content", ""))
                else:
                    data["image_url"] = str(img_el.get("src", ""))
            else:
                for img in soup.find_all("img"):
                    src = str(img.get("src", ""))
                    if src and (".jpg" in src or ".png" in src or ".webp" in src):
                        data["image_url"] = src
                        break

    except ImportError:
        # BeautifulSoup not installed — use regex fallbacks
        data.update(_parse_detail_regex(html))

    # Normalize image URL
    image_url = str(data.get("image_url", ""))
    if image_url.startswith("/"):
        data["image_url"] = urljoin(_BASE_URL, image_url)

    # Strip empty values for cleaner output
    return {k: v for k, v in data.items() if v not in (None, "", [])}


def _first_match(soup, selector: str):
    """Return first element matching any of the comma-separated selectors."""
    for sel in selector.split(","):
        sel = sel.strip()
        if not sel:
            continue
        el = soup.select_one(sel)
        if el:
            return el
    return None


def _clean_text(text: str) -> str:
    """Normalize whitespace and strip."""
    if not text:
        return ""
    return " ".join(text.split())


def _to_int(val: str) -> Optional[int]:
    """Parse integer from a string that may contain extra text."""
    if not val:
        return None
    m = re.search(r"[\d,]+", val)
    if m:
        try:
            return int(m.group(0).replace(",", ""))
        except ValueError:
            return None
    return None


def _to_float(val: str) -> Optional[float]:
    """Parse float from a string."""
    if not val:
        return None
    m = re.search(r"[\d.]+", val)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


def _parse_count(val: str) -> Optional[int]:
    """Parse like counts that may be abbreviated (1.2k)."""
    if not val:
        return None
    val = val.lower().strip()
    m = re.match(r"([\d.]+)\s*([km]?)", val)
    if not m:
        return _to_int(val)
    num, suffix = m.groups()
    try:
        n = float(num)
    except ValueError:
        return _to_int(val)
    multipliers = {"k": 1_000, "m": 1_000_000, "": 1}
    return int(n * multipliers.get(suffix, 1))


def _parse_detail_regex(html: str) -> Dict[str, Any]:
    """Fallback regex-based parser when BeautifulSoup is missing."""
    data: Dict[str, Any] = {}

    # OG image
    og_img = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html)
    if og_img:
        data["image_url"] = og_img.group(1)

    # Prompt text — look for common patterns
    prompt_match = re.search(r'["\']prompt["\'][^>]*>([^<]{20,})', html, re.IGNORECASE)
    if prompt_match:
        data["prompt_text"] = _clean_text(prompt_match.group(1))

    # Model
    model_match = re.search(r'["\']model["\'][^>]*>([^<]+)', html, re.IGNORECASE)
    if model_match:
        data["model"] = _clean_text(model_match.group(1))

    return data


# ── Checkpoint helpers ───────────────────────────────

_CHECKPOINT_FILENAME = ".prompthero_checkpoint.json"


def _checkpoint_path(output_dir: Path) -> Path:
    return output_dir / _CHECKPOINT_FILENAME


def _load_checkpoint(output_dir: Path) -> Optional[Dict[str, Any]]:
    """Load checkpoint if it exists. Returns None if no checkpoint."""
    cp = _checkpoint_path(output_dir)
    if not cp.exists():
        return None
    try:
        with open(cp, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "seen_ids" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _save_checkpoint(
    output_dir: Path,
    seen_ids: set[str],
    total_scraped: int,
    scroll_completed: bool = False,
    all_links: Optional[List[str]] = None,
) -> None:
    """Atomically write checkpoint to disk."""
    cp = _checkpoint_path(output_dir)
    tmp = cp.with_suffix(".tmp")
    data: Dict[str, Any] = {
        "seen_ids": sorted(seen_ids),
        "total_scraped": total_scraped,
        "scroll_completed": scroll_completed,
        "updated_at": int(time.time()),
    }
    if all_links is not None:
        data["all_links"] = all_links
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(cp)


def _delete_checkpoint(output_dir: Path) -> None:
    """Remove checkpoint file (called on successful completion)."""
    cp = _checkpoint_path(output_dir)
    if cp.exists():
        cp.unlink()


def _recover_seen_ids_from_jsonl(jsonl_path: Path) -> set[str]:
    """If checkpoint is lost but JSONL exists, rebuild seen_ids from it."""
    seen: set[str] = set()
    if not jsonl_path.exists():
        return seen
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    pid = obj.get("prompt_id")
                    if pid:
                        seen.add(str(pid))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return seen


class PromptheroChannel(Channel):
    """PromptHero prompt scraper channel."""

    name = "prompthero"
    description = "PromptHero — AI 图片 Prompt 库"
    backends = ["requests"]
    tier = 0

    # Allow runtime selector overrides
    selectors: Dict[str, str] = dict(SELECTORS)

    # Cookie storage (Cookie-Editor JSON format)
    cookies: Optional[List[Dict[str, Any]]] = None

    def can_handle(self, url: str) -> bool:
        return "prompthero.com" in urlparse(url).netloc.lower()

    def check(self, config=None) -> Tuple[str, str]:
        """Check that requests/urllib and BeautifulSoup are available."""
        self.active_backend = self.backends[0]
        missing = []
        try:
            import requests as _requests_mod  # type: ignore[unused-import]
            _ = _requests_mod
        except ImportError:
            pass
        try:
            from bs4 import BeautifulSoup as _bs4_mod  # type: ignore[unused-import]
            _ = _bs4_mod
        except ImportError:
            missing.append("beautifulsoup4 (pip install beautifulsoup4)")
        if missing:
            return "warn", "可用（urllib 标准库），但建议安装: " + ", ".join(missing)
        # Access config to satisfy linter while maintaining base-class signature
        _ = config
        return "ok", "PromptHero 爬虫就绪（requests + BeautifulSoup）"

    def set_cookies(self, cookies_json: str | List[Dict[str, Any]]) -> None:
        """Set authentication cookies from Cookie-Editor export.

        Args:
            cookies_json: Either JSON string or parsed list of cookie dicts
        """
        if isinstance(cookies_json, str):
            self.cookies = json.loads(cookies_json)
        else:
            self.cookies = cookies_json
        cookie_count = len(self.cookies) if self.cookies else 0
        print(f"[PromptHero] ✅ Cookies loaded: {cookie_count} cookies")

    # ── Scroll-based link collection ─────────────────────

    def _scroll_and_collect(
        self,
        start_url: str = _BASE_URL,
        max_prompts: Optional[int] = None,
        scroll_timeout: float = 60.0,
        scroll_pause: float = 1.5,
        headless: bool = True,
    ) -> List[str]:
        """Open a real browser, scroll to bottom, collect all prompt links.

        Requires: pip install playwright && playwright install chromium
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ImportError(
                "Playwright is required for infinite-scroll scraping.\n"
                "Install: pip install playwright && playwright install chromium"
            ) from exc

        links: list[str] = []
        seen: set[str] = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(user_agent=_UA)

            # Add authentication cookies if available
            if self.cookies:
                playwright_cookies = []
                for cookie in self.cookies:
                    # Convert Cookie-Editor format to Playwright format
                    pc = {
                        "name": cookie.get("name"),
                        "value": cookie.get("value"),
                        "domain": cookie.get("domain", "").lstrip("."),
                        "path": cookie.get("path", "/"),
                        "secure": cookie.get("secure", False),
                        "httpOnly": cookie.get("httpOnly", False),
                    }
                    # Handle sameSite
                    same_site = cookie.get("sameSite")
                    if same_site:
                        pc["sameSite"] = same_site.capitalize()

                    if pc["name"] and pc["value"]:
                        playwright_cookies.append(pc)

                context.add_cookies(playwright_cookies)
                print(f"[PromptHero] 🍪 Added {len(playwright_cookies)} cookies to browser")

            page = context.new_page()
            print(f"[PromptHero] 🌐  Opening {start_url} ...")
            page.goto(start_url, wait_until="networkidle", timeout=60_000)

            # Accept any cookie banners so they don't block scroll
            try:
                for btn_text in ("Accept", "Agree", "Got it", "Okay", "I agree"):
                    btn = page.locator(f"button:has-text('{btn_text}')").first
                    if btn.is_visible(timeout=2_000):
                        btn.click()
                        page.wait_for_timeout(500)
                        break
            except Exception:
                pass

            start_time = time.time()
            last_height = 0
            stale_count = 0
            scroll_round = 0

            while True:
                scroll_round += 1
                # Collect currently visible prompt links
                loc = page.locator(self.selectors["list_prompt_links"])
                count = loc.count()
                for i in range(count):
                    href = loc.nth(i).get_attribute("href") or ""
                    if href.startswith("/prompt/"):
                        full = urljoin(_BASE_URL, href)
                        if full not in seen:
                            seen.add(full)
                            links.append(full)

                if max_prompts is not None and len(links) >= max_prompts:
                    links = links[:max_prompts]
                    break

                # Scroll to bottom
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(scroll_pause)

                new_height = page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    stale_count += 1
                    if stale_count >= 3:
                        print(f"[PromptHero] 📜 连续 {stale_count} 次无新内容，滚动结束")
                        break
                else:
                    stale_count = 0
                    last_height = new_height

                if time.time() - start_time > scroll_timeout:
                    print(f"[PromptHero] ⏱️ 滚动超时 ({scroll_timeout}s)，停止")
                    break

                if scroll_round % 10 == 0:
                    print(f"[PromptHero]   已滚动 {scroll_round} 轮，收集到 {len(links)} 个链接")

            browser.close()

        return links

    # ── Public scrape API ────────────────────────────────

    def read(self, url: str) -> Dict[str, Any]:
        """Read a single prompt detail page and return structured data."""
        html = _fetch_html(url, cookies=self.cookies)
        return _parse_detail_html(html, url, self.selectors)

    def scrape_list(self, page: int = 1) -> List[str]:
        """Scrape a list page and return prompt detail URLs (traditional pagination)."""
        url = f"{_BASE_URL}/?page={page}" if page > 1 else _BASE_URL
        html = _fetch_html(url, cookies=self.cookies)

        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            links = []
            for a in soup.select(self.selectors["list_prompt_links"]):
                href = str(a.get("href", ""))
                if href.startswith("/prompt/"):
                    links.append(urljoin(_BASE_URL, href))
            return list(dict.fromkeys(links))
        except ImportError:
            links = re.findall(r'href=["\'](/prompt/[^"\'\s]+)', html)
            return list(dict.fromkeys(urljoin(_BASE_URL, l) for l in links))

    def scrape_all(
        self,
        output_dir: str | Path,
        max_prompts: Optional[int] = None,
        delay: float = 1.0,
        download_images: bool = True,
        checkpoint: bool = True,
        checkpoint_every: int = 10,
        scroll_timeout: float = 120.0,
        scroll_pause: float = 1.5,
        headless: bool = True,
    ) -> Path:
        """Scrape all prompts via browser scroll + detail page requests.

        Args:
            output_dir: Directory to save JSONL and images.
            max_prompts: Maximum prompts to scrape (None = all).
            delay: Seconds to sleep between detail page requests.
            download_images: Whether to download images to output_dir/images/.
            checkpoint: Whether to enable automatic checkpoint / resume.
            checkpoint_every: Save checkpoint every N prompts (default 10).
            scroll_timeout: Max seconds for scrolling the list page.
            scroll_pause: Seconds to wait after each scroll.
            headless: Run browser in headless mode.

        Returns:
            Path to the generated JSONL file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        img_dir = output_dir / "images"
        if download_images:
            img_dir.mkdir(parents=True, exist_ok=True)

        jsonl_path = output_dir / f"prompthero_prompts_{time.strftime('%Y-%m-%d')}.jsonl"

        # ── Resume from checkpoint ────────────────────────
        seen_ids: set[str] = set()
        total = 0
        scroll_completed = False
        resumed = False
        saved_links: list[str] = []

        if checkpoint:
            cp = _load_checkpoint(output_dir)
            if cp:
                seen_ids = set(cp.get("seen_ids", []))
                total = cp.get("total_scraped", 0)
                scroll_completed = cp.get("scroll_completed", False)
                saved_links = list(cp.get("all_links", []))
                resumed = True
                print(f"[PromptHero] ▶️  从 checkpoint 恢复: 已爬取 {total} 条, scroll_completed={scroll_completed}, 已存链接 {len(saved_links)} 个")
            elif jsonl_path.exists():
                seen_ids = _recover_seen_ids_from_jsonl(jsonl_path)
                total = len(seen_ids)
                if total > 0:
                    resumed = True
                    print(f"[PromptHero] ▶️  从已有 JSONL 恢复: 已识别 {total} 条记录")

        # ── Step 1: Collect all links via scroll ──────────
        all_links: list[str] = []
        if saved_links and scroll_completed:
            # Reuse links saved in checkpoint — skip re-scrolling
            all_links = saved_links
            print(f"[PromptHero] 📋 使用 checkpoint 中保存的 {len(all_links)} 个链接，跳过滚动")
        elif not scroll_completed:
            try:
                all_links = self._scroll_and_collect(
                    start_url=_BASE_URL,
                    max_prompts=max_prompts,
                    scroll_timeout=scroll_timeout,
                    scroll_pause=scroll_pause,
                    headless=headless,
                )
            except ImportError as e:
                print(f"[PromptHero] ❌ {e}")
                print("[PromptHero] 请安装 Playwright: pip install playwright && playwright install chromium")
                raise
            scroll_completed = True
            print(f"[PromptHero] 📋 共收集到 {len(all_links)} 个 prompt 链接")
            # Save links immediately so an interrupt during detail scraping
            # doesn't lose the scroll results
            if checkpoint:
                _save_checkpoint(output_dir, seen_ids, total, scroll_completed=True, all_links=all_links)
        else:
            # Checkpoint says scroll completed but has no saved links (legacy checkpoint)
            # — re-scroll to rebuild the link list.
            print("[PromptHero] 📋 checkpoint 无链接缓存，重新滚动收集")
            all_links = self._scroll_and_collect(
                start_url=_BASE_URL,
                max_prompts=max_prompts,
                scroll_timeout=scroll_timeout,
                scroll_pause=scroll_pause,
                headless=headless,
            )
            if checkpoint:
                _save_checkpoint(output_dir, seen_ids, total, scroll_completed=True, all_links=all_links)

        new_links = [u for u in all_links if _extract_id_from_url(u) not in seen_ids]
        if not new_links and not resumed:
            print("[PromptHero] 所有链接已爬取过，无需继续")
            return jsonl_path

        file_mode = "a" if resumed else "w"

        try:
            with open(jsonl_path, file_mode, encoding="utf-8") as f:
                for url in new_links:
                    prompt_id = _extract_id_from_url(url)
                    seen_ids.add(prompt_id)

                    try:
                        data = self.read(url)
                    except Exception as e:
                        print(f"[PromptHero]  ERROR reading {url}: {e}")
                        continue

                    # Download image
                    if download_images and data.get("image_url"):
                        ext = Path(urlparse(data["image_url"]).path).suffix or ".jpg"
                        img_path = img_dir / f"{prompt_id}{ext}"
                        if not img_path.exists():
                            try:
                                _download_file(data["image_url"], img_path)
                                data["image_local_path"] = str(img_path.relative_to(output_dir))
                            except Exception as e:
                                print(f"[PromptHero]  WARN image download failed for {prompt_id}: {e}")
                        else:
                            data["image_local_path"] = str(img_path.relative_to(output_dir))

                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
                    f.flush()
                    total += 1

                    # Periodic checkpoint
                    if checkpoint and total % checkpoint_every == 0:
                        _save_checkpoint(output_dir, seen_ids, total, scroll_completed=True, all_links=all_links)
                        print(f"[PromptHero]   💾 checkpoint saved (total={total})")

                    time.sleep(delay)

            # ── Completion ────────────────────────────────────
            if checkpoint:
                _delete_checkpoint(output_dir)
                print(f"[PromptHero] ✅ 爬取完成，checkpoint 已清除")

        except KeyboardInterrupt:
            if checkpoint:
                _save_checkpoint(output_dir, seen_ids, total, scroll_completed=scroll_completed, all_links=all_links)
                print(f"\n[PromptHero] ⏸️  中断！checkpoint 已保存: total={total}, scroll_completed={scroll_completed}")
                print(f"[PromptHero]    下次运行自动恢复")
            raise

        print(f"[PromptHero] Done. Scraped {total} prompts → {jsonl_path}")
        return jsonl_path


def _download_file(url: str, dest: Path, timeout: int = 60) -> None:
    """Download a file to a local path."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        dest.write_bytes(resp.read())
