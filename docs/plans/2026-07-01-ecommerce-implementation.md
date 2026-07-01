# E-Commerce Platform Support — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **Design doc:** `docs/plans/2026-07-01-ecommerce-design.md`

**Goal:** Build `ecommerce-cli` (independent CLI tool) + integrate 14 e-commerce Channels into Agent-Reach.

**Architecture:** `ecommerce-cli` is a standalone Python CLI that uses Playwright for browser automation, stealth plugins for anti-detection, and cookie profiles for auth. Agent-Reach Channels call `ecommerce-cli <platform> check` for health checks; Agents call `ecommerce-cli <platform> search/read/reviews` directly.

**Tech Stack:** Python 3.10+, Playwright, Click (CLI framework), rookiepy (cookie extraction)

**Implementation order:** Core engine → 1 easy platform (Best Buy) to validate → expand to all platforms → Agent-Reach Channel registration → docs.

---

### Task 1: Create ecommerce-cli project skeleton

**Location:** `/Users/majing/projects/ecommerce-cli/` (sibling to Agent-Reach)

**Files:**
- Create: `pyproject.toml`
- Create: `ecommerce_cli/__init__.py`
- Create: `ecommerce_cli/cli.py`
- Create: `ecommerce_cli/engine.py`
- Create: `ecommerce_cli/platforms/__init__.py`
- Create: `ecommerce_cli/platforms/base.py`

**Step 1: Create project directory**

```bash
mkdir -p /Users/majing/projects/ecommerce-cli/ecommerce_cli/platforms
```

**Step 2: Write `pyproject.toml`**

```toml
[project]
name = "ecommerce-cli"
version = "0.1.0"
description = "CLI tool for searching and scraping e-commerce platforms via browser automation"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.10"
authors = [{name = "Agent Reach Contributors"}]
dependencies = [
    "click>=8.0",
    "playwright>=1.40",
    "rookiepy>=0.4",
    "pydantic>=2.0",
]

[project.scripts]
ecommerce-cli = "ecommerce_cli.cli:main"

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["ecommerce_cli*"]
```

**Step 3: Write `ecommerce_cli/__init__.py`**

```python
"""ecommerce-cli — search and scrape e-commerce platforms via browser automation."""
__version__ = "0.1.0"
```

**Step 4: Dev install**

```bash
cd /Users/majing/projects/ecommerce-cli && pip install -e .
```

**Step 5: Commit**

```bash
cd /Users/majing/projects/ecommerce-cli && git init && git add -A && git commit -m "feat: ecommerce-cli project skeleton"
```

---

### Task 2: Core Playwright engine with stealth

**Files:**
- Create: `ecommerce_cli/engine.py`
- Create: `ecommerce_cli/stealth.py`

**Step 1: Write the stealth module**

`ecommerce_cli/stealth.py` — anti-detection script injected into every page:

```python
"""Browser stealth — hide automation traces from anti-bot detection."""

STEALTH_SCRIPT = """
// Overwrite navigator.webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => false });

// Overwrite chrome object
window.chrome = { runtime: {} };

// Overwrite permissions query
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
    Promise.resolve({ state: Notification.permission }) :
    originalQuery(parameters)
);

// Overwrite plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});

// Overwrite languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
});

// Remove PhantomJS traces
delete window.callPhantom;
delete window._phantom;
delete window.__phantomas;
"""
```

**Step 2: Write the engine module**

`ecommerce_cli/engine.py`:

```python
"""Shared Playwright engine with cookie management, stealth, and rate limiting."""

import json
import os
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from .stealth import STEALTH_SCRIPT

DEFAULT_CONFIG_DIR = Path.home() / ".ecommerce-cli"
DEFAULT_PROFILES_DIR = DEFAULT_CONFIG_DIR / "profiles"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.yaml"


class EcommerceEngine:
    """Manages Playwright browser, cookie profiles, and stealth injection."""

    def __init__(
        self,
        platform: str,
        headless: bool = True,
        timeout: int = 30000,
        proxy: Optional[str] = None,
    ):
        self.platform = platform
        self.headless = headless
        self.timeout = timeout
        self.proxy = proxy
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    def start(self) -> "EcommerceEngine":
        """Launch browser and create a stealth context."""
        self._playwright = sync_playwright().start()
        launch_args = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
            ],
        }
        if self.proxy:
            launch_args["proxy"] = {"server": self.proxy}

        self._browser = self._playwright.chromium.launch(**launch_args)
        context_args = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "locale": "zh-CN",
        }
        # Load persisted profile if available
        profile_path = DEFAULT_PROFILES_DIR / f"{self.platform}.json"
        if profile_path.exists():
            context_args["storage_state"] = str(profile_path)

        self._context = self._browser.new_context(**context_args)
        self._context.add_init_script(STEALTH_SCRIPT)
        return self

    def new_page(self) -> Page:
        """Create a new page with stealth already injected."""
        if not self._context:
            self.start()
        page = self._context.new_page()
        page.set_default_timeout(self.timeout)
        return page

    def save_profile(self) -> None:
        """Persist cookies + storage state to disk."""
        if not self._context:
            return
        DEFAULT_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        state = self._context.storage_state()
        profile_path = DEFAULT_PROFILES_DIR / f"{self.platform}.json"
        with open(profile_path, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def has_profile(self) -> bool:
        """Check if a saved cookie profile exists for this platform."""
        return (DEFAULT_PROFILES_DIR / f"{self.platform}.json").exists()

    def stop(self) -> None:
        """Clean up browser resources."""
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()
```

**Step 3: Install Playwright browsers**

```bash
cd /Users/majing/projects/ecommerce-cli && python -m playwright install chromium
```

**Step 4: Smoke test — launch and close**

```python
from ecommerce_cli.engine import EcommerceEngine
with EcommerceEngine("test", headless=True) as engine:
    page = engine.new_page()
    page.goto("https://httpbin.org/headers")
    print(page.content()[:200])
```

Run: `python -c "..."` — should print HTML without errors.

**Step 5: Commit**

```bash
cd /Users/majing/projects/ecommerce-cli && git add -A && git commit -m "feat: core Playwright engine with stealth injection"
```

---

### Task 3: Platform base class and CLI entry point

**Files:**
- Create: `ecommerce_cli/platforms/base.py`
- Modify: `ecommerce_cli/cli.py`

**Step 1: Write platform base class**

`ecommerce_cli/platforms/base.py`:

```python
"""Base class for e-commerce platform implementations."""

from abc import ABC, abstractmethod
from typing import Any

from ..engine import EcommerceEngine


class BasePlatform(ABC):
    """Each e-commerce platform implements search / read / reviews."""

    name: str = ""           # e.g. "bestbuy"
    domains: list[str] = []  # e.g. ["bestbuy.com"]
    currency: str = "USD"

    def __init__(self, engine: EcommerceEngine):
        self.engine = engine

    def can_handle(self, url: str) -> bool:
        """Check if this platform handles the given URL."""
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        return any(d in domain for d in self.domains)

    @abstractmethod
    def search(self, query: str, limit: int = 10, **kwargs) -> list[dict]:
        """Search for products. Returns list of product dicts."""
        ...

    @abstractmethod
    def read(self, url: str) -> dict:
        """Read product details from a URL. Returns a product dict."""
        ...

    def reviews(self, url: str, pages: int = 1) -> list[dict]:
        """Fetch product reviews. Optional — default returns empty."""
        return []

    def check(self) -> tuple[str, str]:
        """Health check. Returns (status, message)."""
        if not self.engine.has_profile():
            return (
                "no-cookie",
                f"{self.name} 未配置 Cookie。"
                f"请先在浏览器登录 {self.domains[0]}，然后运行："
                f"ecommerce-cli {self.name} configure --from-browser chrome",
            )
        try:
            page = self.engine.new_page()
            page.goto(f"https://www.{self.domains[0]}/", wait_until="domcontentloaded")
            title = page.title()
            page.close()
            if title:
                return "ok", f"{self.name} 已配置，可正常访问"
            return "warn", f"{self.name} 页面加载异常"
        except Exception as e:
            return "error", f"{self.name} 连接失败：{e}"
```

**Step 2: Write CLI entry point**

`ecommerce_cli/cli.py`:

```python
"""ecommerce-cli — search and scrape e-commerce platforms."""

import json
import sys

import click

from .engine import EcommerceEngine
from .platforms import get_platform


def _print_json(data):
    """Print data as JSON to stdout."""
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


@click.group()
def main():
    """ecommerce-cli — search and scrape e-commerce platforms."""
    pass


@main.command()
@click.argument("platform")
@click.argument("query")
@click.option("-n", "--limit", default=10, help="Max results")
@click.option("--min-price", type=float, help="Minimum price filter")
@click.option("--max-price", type=float, help="Maximum price filter")
@click.option("--headless/--no-headless", default=True, help="Run browser headless")
def search(platform, query, limit, min_price, max_price, headless):
    """Search for products on a platform."""
    platform_cls = get_platform(platform)
    if not platform_cls:
        click.echo(f"Unknown platform: {platform}", err=True)
        sys.exit(1)

    with EcommerceEngine(platform, headless=headless) as engine:
        p = platform_cls(engine)
        results = p.search(query, limit=limit)
        _print_json({"platform": platform, "query": query, "items": results})


@main.command()
@click.argument("platform")
@click.argument("url")
@click.option("--headless/--no-headless", default=True)
def read(platform, url, headless):
    """Read product details from a URL."""
    platform_cls = get_platform(platform)
    if not platform_cls:
        click.echo(f"Unknown platform: {platform}", err=True)
        sys.exit(1)

    with EcommerceEngine(platform, headless=headless) as engine:
        p = platform_cls(engine)
        result = p.read(url)
        _print_json(result)


@main.command()
@click.argument("platform")
@click.argument("url")
@click.option("--pages", default=1, help="Pages of reviews to fetch")
@click.option("--headless/--no-headless", default=True)
def reviews(platform, url, pages, headless):
    """Fetch product reviews."""
    platform_cls = get_platform(platform)
    if not platform_cls:
        click.echo(f"Unknown platform: {platform}", err=True)
        sys.exit(1)

    with EcommerceEngine(platform, headless=headless) as engine:
        p = platform_cls(engine)
        result = p.reviews(url, pages=pages)
        _print_json({"platform": platform, "url": url, "reviews": result})


@main.command()
@click.argument("platform")
@click.option("--headless/--no-headless", default=True)
def check(platform, headless):
    """Health check — is this platform configured and working?"""
    platform_cls = get_platform(platform)
    if not platform_cls:
        print(json.dumps({"status": "error", "message": f"Unknown platform: {platform}"}))
        sys.exit(1)

    with EcommerceEngine(platform, headless=headless) as engine:
        p = platform_cls(engine)
        status, message = p.check()
        print(json.dumps({"platform": platform, "status": status, "message": message}))


if __name__ == "__main__":
    main()
```

**Step 3: Write platform registry**

`ecommerce_cli/platforms/__init__.py`:

```python
"""Platform registry — maps platform names to implementation classes."""

from typing import Optional

from .base import BasePlatform

# Will be populated as platforms are added
PLATFORMS: dict[str, type[BasePlatform]] = {}


def register_platform(cls):
    """Decorator to register a platform implementation."""
    PLATFORMS[cls.name] = cls
    return cls


def get_platform(name: str) -> Optional[type[BasePlatform]]:
    """Look up a platform by name."""
    return PLATFORMS.get(name)


def list_platforms() -> list[str]:
    """List all registered platform names."""
    return sorted(PLATFORMS.keys())
```

**Step 4: Commit**

```bash
cd /Users/majing/projects/ecommerce-cli && git add -A && git commit -m "feat: platform base class and CLI entry point"
```

---

### Task 4: Best Buy platform — first implementation (proof of concept)

**Why Best Buy first:** Weakest anti-crawl, clean HTML structure, no login required for search.

**Files:**
- Create: `ecommerce_cli/platforms/bestbuy.py`
- Modify: `ecommerce_cli/platforms/__init__.py`

**Step 1: Write Best Buy platform**

`ecommerce_cli/platforms/bestbuy.py`:

```python
"""Best Buy (bestbuy.com) — product search and details."""

import re
from typing import Any

from .base import BasePlatform
from . import register_platform


@register_platform
class BestBuyPlatform(BasePlatform):
    name = "bestbuy"
    domains = ["bestbuy.com", "www.bestbuy.com"]
    currency = "USD"

    SEARCH_URL = "https://www.bestbuy.com/site/searchpage.jsp?st={query}"
    PRODUCT_SELECTOR = ".sku-item"
    TITLE_SELECTOR = ".sku-title a"
    PRICE_SELECTOR = ".priceView-customer-price span"
    IMAGE_SELECTOR = ".product-image img"
    RATING_SELECTOR = ".c-reviews-v4 .c-reviews span"

    def search(self, query: str, limit: int = 10, **kwargs) -> list[dict]:
        """Search Best Buy for products."""
        from urllib.parse import quote

        page = self.engine.new_page()
        url = self.SEARCH_URL.format(query=quote(query))
        page.goto(url, wait_until="networkidle")

        # Wait for product list to render
        try:
            page.wait_for_selector(self.PRODUCT_SELECTOR, timeout=10000)
        except Exception:
            page.close()
            return []

        # Extract product cards
        items = []
        cards = page.query_selector_all(self.PRODUCT_SELECTOR)
        for card in cards[:limit]:
            try:
                title_el = card.query_selector(self.TITLE_SELECTOR)
                price_el = card.query_selector(self.PRICE_SELECTOR)
                image_el = card.query_selector(self.IMAGE_SELECTOR)
                rating_el = card.query_selector(self.RATING_SELECTOR)

                title = title_el.inner_text().strip() if title_el else ""
                href = title_el.get_attribute("href") if title_el else ""
                if href and not href.startswith("http"):
                    href = f"https://www.bestbuy.com{href}"

                price_text = price_el.inner_text().strip() if price_el else ""
                price = self._parse_price(price_text)

                image = image_el.get_attribute("src") if image_el else ""

                rating_text = rating_el.inner_text().strip() if rating_el else ""
                rating = self._parse_rating(rating_text)

                items.append({
                    "title": title,
                    "price": price,
                    "currency": self.currency,
                    "url": href,
                    "image": image,
                    "rating": rating,
                    "platform": self.name,
                })
            except Exception:
                continue

        page.close()
        return items

    def read(self, url: str) -> dict:
        """Read product details from a Best Buy URL."""
        page = self.engine.new_page()
        page.goto(url, wait_until="networkidle")

        result: dict[str, Any] = {"url": url, "platform": self.name}

        try:
            result["title"] = page.title().replace(" - Best Buy", "").strip()
        except Exception:
            result["title"] = ""

        # Price
        try:
            price_el = page.query_selector(".priceView-customer-price span")
            if price_el:
                price_text = price_el.inner_text().strip()
                result["price"] = self._parse_price(price_text)
                result["currency"] = self.currency
        except Exception:
            result["price"] = None

        # Description / specs
        try:
            desc_el = page.query_selector(".shop-product-description")
            result["description"] = desc_el.inner_text().strip()[:2000] if desc_el else ""
        except Exception:
            result["description"] = ""

        # Rating
        try:
            rating_el = page.query_selector(".c-reviews-v4 .c-reviews span")
            if rating_el:
                result["rating"] = self._parse_rating(rating_el.inner_text())
        except Exception:
            result["rating"] = None

        # Availability
        try:
            avail_el = page.query_selector(".fulfillment-add-to-cart-button button")
            if avail_el:
                btn_text = avail_el.inner_text().strip().lower()
                result["in_stock"] = "add to cart" in btn_text or "pre-order" in btn_text
            else:
                result["in_stock"] = False
        except Exception:
            result["in_stock"] = False

        page.close()
        return result

    def reviews(self, url: str, pages: int = 1) -> list[dict]:
        """Fetch Best Buy product reviews."""
        results = []
        for page_num in range(1, pages + 1):
            reviews_url = f"{url}?page={page_num}" if page_num > 1 else url
            page = self.engine.new_page()
            page.goto(reviews_url, wait_until="networkidle")

            try:
                page.wait_for_selector(".review-item", timeout=10000)
                items = page.query_selector_all(".review-item")
                for item in items:
                    try:
                        reviewer = item.query_selector(".reviewer-name")
                        rating = item.query_selector(".c-review-average")
                        body = item.query_selector(".pre-white-space")
                        title = item.query_selector(".review-title")

                        results.append({
                            "reviewer": reviewer.inner_text().strip() if reviewer else "",
                            "rating": self._parse_rating(rating.inner_text()) if rating else None,
                            "title": title.inner_text().strip() if title else "",
                            "body": body.inner_text().strip() if body else "",
                        })
                    except Exception:
                        continue
            except Exception:
                pass

            page.close()

        return results

    @staticmethod
    def _parse_price(text: str) -> float | None:
        """Parse price string like '$1,299.99' to float."""
        match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
        return float(match.group()) if match else None

    @staticmethod
    def _parse_rating(text: str) -> float | None:
        """Parse rating string like '4.5(123)' to float."""
        match = re.search(r"(\d+\.?\d*)", text)
        return float(match.group(1)) if match else None
```

**Step 2: Import in platforms `__init__.py`**

Add to `ecommerce_cli/platforms/__init__.py`:

```python
from . import bestbuy  # noqa: F401 — register BestBuyPlatform
```

**Step 3: Manual smoke test**

```bash
cd /Users/majing/projects/ecommerce-cli
python -m ecommerce_cli.cli search bestbuy "laptop" -n 5
python -m ecommerce_cli.cli read bestbuy "https://www.bestbuy.com/site/some-product"
python -m ecommerce_cli.cli check bestbuy
```

**Step 4: Commit**

```bash
cd /Users/majing/projects/ecommerce-cli && git add -A && git commit -m "feat: Best Buy platform — search, read, reviews"
```

---

### Task 5: Agent-Reach — Best Buy Channel

**Files:**
- Create: `agent_reach/channels/bestbuy.py`
- Modify: `agent_reach/channels/__init__.py`

**Step 1: Write Best Buy Channel**

`agent_reach/channels/bestbuy.py`:

```python
# -*- coding: utf-8 -*-
"""Best Buy — product search and details via ecommerce-cli."""

from .base import Channel
from ..probe import probe_command


class BestBuyChannel(Channel):
    name = "bestbuy"
    description = "Best Buy 商品搜索与详情"
    backends = ["ecommerce-cli"]
    tier = 0  # No login required for search

    def can_handle(self, url: str) -> bool:
        return "bestbuy.com" in url.lower()

    def check(self, config=None):
        self.active_backend = None

        # Probe ecommerce-cli installation
        result = probe_command("ecommerce-cli", ["bestbuy", "check"], timeout=15)
        if result.status == "missing":
            return "off", (
                "ecommerce-cli 未安装。安装：pipx install ecommerce-cli\n"
                "然后运行：python -m playwright install chromium"
            )
        if result.status == "broken":
            return "error", f"ecommerce-cli 已损坏：{result.hint}"

        # Parse check output
        try:
            import json
            data = json.loads(result.output.split("\n")[-1])
            if data.get("status") == "ok":
                self.active_backend = self.backends[0]
                return "ok", data.get("message", "Best Buy 搜索与详情可用（无需登录）")
            return "warn", data.get("message", "Best Buy 未完全配置")
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
```

**Step 2: Register in channels `__init__.py`**

Add import:
```python
from .bestbuy import BestBuyChannel
```

Add instance to `ALL_CHANNELS`:
```python
BestBuyChannel(),
```

**Step 3: Run existing tests**

```bash
cd /Users/majing/projects/Agent-Reach && pytest tests/ -v
```

**Step 4: Commit**

```bash
cd /Users/majing/projects/Agent-Reach && git add -A && git commit -m "feat(channel): add Best Buy e-commerce channel"
```

---

### Task 6: Cookie configuration command

**Files:**
- Modify: `ecommerce_cli/cli.py` (add `configure` command)
- Create: `ecommerce_cli/cookie_manager.py`

**Step 1: Write cookie manager**

`ecommerce_cli/cookie_manager.py`:

```python
"""Cookie extraction and injection for ecommerce-cli."""

import json
from pathlib import Path
from typing import Optional


def extract_from_browser(domain: str) -> Optional[str]:
    """Extract cookies for a domain from the local Chrome browser.

    Returns a JSON string suitable for storage_state injection.
    """
    try:
        import rookiepy
        cookies = rookiepy.chrome([domain])
        if not cookies:
            return None
        # Convert to Playwright storage_state format
        state = {
            "cookies": [
                {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", domain),
                    "path": c.get("path", "/"),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", True),
                    "sameSite": c.get("sameSite", "Lax"),
                }
                for c in cookies
            ],
            "origins": [],
        }
        return json.dumps(state, ensure_ascii=False)
    except ImportError:
        try:
            import browser_cookie3
            cookies = list(browser_cookie3.chrome(domain_name=domain))
            if not cookies:
                return None
            state = {
                "cookies": [
                    {
                        "name": c.name,
                        "value": c.value,
                        "domain": c.domain or domain,
                        "path": c.path or "/",
                        "httpOnly": getattr(c, "httpOnly", False),
                        "secure": getattr(c, "secure", True),
                        "sameSite": "Lax",
                    }
                    for c in cookies
                ],
                "origins": [],
            }
            return json.dumps(state, ensure_ascii=False)
        except ImportError:
            return None


def save_profile(platform: str, state_json: str) -> Path:
    """Save a Playwright storage_state JSON to the platform profile."""
    from .engine import DEFAULT_PROFILES_DIR
    DEFAULT_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profile_path = DEFAULT_PROFILES_DIR / f"{platform}.json"
    # Validate JSON
    state = json.loads(state_json)
    with open(profile_path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    return profile_path
```

**Step 2: Add configure command to CLI**

Add to `ecommerce_cli/cli.py`:

```python
@main.command()
@click.argument("platform")
@click.option("--from-browser", "browser", default="chrome", help="Browser to extract cookies from")
@click.option("--cookie", "cookie_str", help="Cookie string in 'name=value; name2=value2' format")
def configure(platform, browser, cookie_str):
    """Configure cookie authentication for a platform."""
    from .cookie_manager import extract_from_browser, save_profile

    platform_cls = get_platform(platform)
    if not platform_cls:
        click.echo(f"Unknown platform: {platform}", err=True)
        sys.exit(1)

    domain = platform_cls.domains[0]

    if cookie_str:
        # Manual cookie string injection
        # Convert cookie string to storage_state format
        cookies = []
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            name, _, value = pair.partition("=")
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": f".{domain}",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            })
        state = json.dumps({"cookies": cookies, "origins": []})
        profile_path = save_profile(platform, state)
        click.echo(f"Saved cookie profile to {profile_path}")
    elif browser:
        click.echo(f"Extracting cookies for {domain} from {browser}...")
        state = extract_from_browser(f".{domain}")
        if state:
            profile_path = save_profile(platform, state)
            click.echo(f"Saved cookie profile to {profile_path}")
        else:
            click.echo(
                f"No cookies found for {domain} in {browser}. "
                f"Make sure you are logged in and {browser} is installed.",
                err=True,
            )
            sys.exit(1)
    else:
        click.echo("Use --from-browser <browser> or --cookie '<string>'", err=True)
        sys.exit(1)
```

**Step 3: Commit**

```bash
cd /Users/majing/projects/ecommerce-cli && git add -A && git commit -m "feat: cookie configuration from browser and manual string"
```

---

### Task 7: Expand to remaining platforms

After Best Buy validates the pattern, implement the remaining 13 platforms following the same template. Each platform needs:

1. `ecommerce_cli/platforms/<name>.py` — `BasePlatform` subclass with selectors
2. Import in `ecommerce_cli/platforms/__init__.py`
3. `agent_reach/channels/<name>.py` — Channel subclass
4. Register in `agent_reach/channels/__init__.py`

**Implementation order (easy → hard):**

| Batch | Platforms | Key challenge |
|---|---|---|
| 1 | Target, Etsy | Simple HTML, no login required |
| 2 | eBay, Walmart | Some anti-bot, mostly simple |
| 3 | AliExpress, Lazada, Shopee | International, varying structures |
| 4 | Amazon | Strong anti-bot, requires careful stealth |
| 5 | 京东 | Moderate anti-crawl, Chinese HTML |
| 6 | 淘宝 + 天猫 | Strong anti-crawl, login required |
| 7 | 拼多多 | Strongest anti-crawl, mobile-first |
| 8 | 闲鱼 | Login required, app-oriented |

Each platform: write platform file → manual test → write channel file → commit.

---

### Task 8: Agent-Reach — extend configure --from-browser for e-commerce

**Files:**
- Modify: `agent_reach/cli.py` or relevant configure module

**Step 1: Add e-commerce domains to the `--from-browser` extraction list**

Find where `configure --from-browser` is implemented and add all 14 e-commerce domains so one command extracts all cookies at once.

**Step 2: For each domain, pipe extracted cookies to `ecommerce-cli configure`**

```python
ECOMMERCE_DOMAINS = {
    "taobao": ".taobao.com",
    "tmall": ".tmall.com",
    "jd": ".jd.com",
    "pinduoduo": ".pinduoduo.com",
    "goofish": ".goofish.com",
    "amazon": ".amazon.com",
    "ebay": ".ebay.com",
    "walmart": ".walmart.com",
    "bestbuy": ".bestbuy.com",
    "shopee": ".shopee.com",
    "lazada": ".lazada.com",
    "aliexpress": ".aliexpress.com",
    "etsy": ".etsy.com",
    "target": ".target.com",
}
```

**Step 3: Commit**

---

### Task 9: Documentation

**Files:**
- Create: `docs/ecommerce.md`
- Modify: `README.md`

**Step 1: Write `docs/ecommerce.md`** — configuration guide for all 14 platforms

**Step 2: Update `README.md`** — add e-commerce section to platform matrix

**Step 3: Update `CLAUDE.md`** — add ecommerce-cli to project description

**Step 4: Commit**

---

### Task 10: Final verification

**Step 1: Run all Agent-Reach tests**

```bash
cd /Users/majing/projects/Agent-Reach && pytest tests/ -v
```

**Step 2: Run doctor to verify all channels appear**

```bash
python -m agent_reach.cli doctor
```

**Step 3: Full integration test**

```bash
bash test.sh
```

**Step 4: Fix any failures, then commit**
