# -*- coding: utf-8 -*-
"""Xiaohongshu note download engine.

Downloads note metadata, images, videos, and comments to local storage
following a MediaCrawler-inspired directory structure:

    {save_path}/{note_id}/
    ├── metadata.json
    ├── images/
    │   ├── 0.jpg
    │   └── ...
    ├── videos/
    │   └── 0.mp4
    └── comments.json   (optional)

Public entry point:
    download_xhs_note(url, *, save_path=None, config=None, ...) -> Path

Designed to be importable from channels (e.g.
XiaoHongShuChannel.download_note) and usable standalone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
import yaml
from loguru import logger

from agent_reach.config import Config
from agent_reach.utils.paths import make_private_dir

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def download_xhs_note(
    url: str,
    save_path: Optional[Path] = None,
    config: Optional[Config] = None,
    download_media: bool = True,
    download_comments: bool = True,
) -> Path:
    """Download a Xiaohongshu note and its media to local storage.

    Args:
        url: Full XHS note URL (must include xsec_token for most backends).
        save_path: Base directory for storage (default: ~/.agent-reach/xiaohongshu/).
        config: Agent Reach Config instance (auto-created if None).
        download_media: Whether to download images and videos.
        download_comments: Whether to fetch and save comments.

    Returns:
        Path to the note directory.

    Raises:
        RuntimeError: No XHS backend is available.
        ValueError: URL parsing or metadata parsing failed.
        subprocess.TimeoutExpired: Upstream command timed out.
    """
    cfg = config or Config()

    # 1. Determine active backend --------------------------------------------
    from agent_reach.channels.xiaohongshu import XiaoHongShuChannel

    ch = XiaoHongShuChannel()
    ch.check(cfg)
    backend = ch.active_backend
    if not backend:
        raise RuntimeError(
            "没有可用的小红书后端。运行 `agent-reach doctor` 检查状态。"
        )
    logger.info(f"XHS download: using backend {backend}")

    # 2. Resolve short URLs --------------------------------------------------
    url = _resolve_short_url(url)

    # 3. Parse URL → note_id, xsec_token -------------------------------------
    parsed = _parse_xhs_url(url)
    note_id = parsed["note_id"]
    xsec_token = parsed.get("xsec_token", "")
    logger.info(f"Parsed note_id={note_id}  xsec_token={xsec_token[:12]}...")

    # 4. Fetch note metadata from upstream tool -------------------------------
    note_data = _fetch_note_metadata(url, note_id, xsec_token, backend)
    logger.info(f"Fetched note: {note_data.get('title', 'Untitled')[:60]}")

    # 5. Prepare save directory -----------------------------------------------
    base = save_path or Path(
        cfg.get("xhs_save_path") or "~/.agent-reach/xiaohongshu"
    ).expanduser().resolve()
    note_dir = make_private_dir(base / note_id)
    logger.info(f"Save directory: {note_dir}")

    # 6. Write metadata.json --------------------------------------------------
    _write_metadata(note_dir, note_data, url)

    media_fetched_via_urls = False

    # 7. Download images ------------------------------------------------------
    image_urls = note_data.get("images", []) or []
    if download_media and image_urls:
        media_fetched_via_urls = True
        img_dir = make_private_dir(note_dir / "images")
        for i, img_url in enumerate(image_urls):
            dest = img_dir / f"{i}.jpg"
            ok = _download_file(img_url, dest)
            if ok:
                logger.info(f"  Image {i}/{len(image_urls)} saved: {dest.name}")
            else:
                logger.warning(f"  Image {i}/{len(image_urls)} failed: {img_url[:80]}")
            if i < len(image_urls) - 1:
                time.sleep(1)  # gentle rate limit

    # 8. Download videos ------------------------------------------------------
    video_urls = _extract_video_urls(note_data)
    if download_media and video_urls:
        media_fetched_via_urls = True
        vid_dir = make_private_dir(note_dir / "videos")
        for i, vid_url in enumerate(video_urls):
            dest = vid_dir / f"{i}.mp4"
            ok = _download_file(vid_url, dest)
            if ok:
                logger.info(f"  Video saved: {dest.name}")
            else:
                logger.warning(f"  Video failed: {vid_url[:80]}")

    # 8b. OpenCLI native media fallback --------------------------------------
    # OpenCLI's `note` metadata carries no image/video URLs; when nothing was
    # fetched above, delegate to `opencli xiaohongshu download` and lay the
    # files out under images/ and videos/.
    if download_media and backend.startswith("OpenCLI") and not media_fetched_via_urls:
        _download_media_opencli(url, note_dir)
        _patch_metadata_local_media(note_dir)

    # 9. Fetch comments (optional) --------------------------------------------
    if download_comments:
        try:
            comments = _fetch_comments(url, note_id, xsec_token, backend)
            if comments:
                comments_path = note_dir / "comments.json"
                comments_path.write_text(
                    json.dumps(comments, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(f"  Comments saved: {len(comments)} comments")
        except Exception as exc:
            logger.warning(f"  Comments fetch skipped: {exc}")

    return note_dir


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _resolve_short_url(url: str) -> str:
    """Follow xhslink.com redirects to get the real URL."""
    parsed = urlparse(url)
    if "xhslink.com" not in parsed.netloc.lower():
        return url
    try:
        resp = requests.head(
            url, allow_redirects=True, timeout=10,
            headers={"User-Agent": USER_AGENT},
        )
        return resp.url
    except requests.RequestException:
        logger.warning("Could not resolve xhslink.com URL, using as-is")
        return url


def _parse_xhs_url(url: str) -> dict:
    """Extract note_id and xsec_token from a Xiaohongshu URL.

    Supported patterns (the same note is served under all of them):
        /explore/{note_id}?xsec_token=...
        /discovery/item/{note_id}?xsec_token=...
        /search_result/{note_id}?xsec_token=...
        /user/profile/{user_id}/{note_id}?xsec_token=...
        /explore/{note_id}  (no xsec_token)
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    query = parse_qs(parsed.query)

    # XHS exposes the same note under several path shapes; try each.
    patterns = (
        r"/explore/([a-fA-F0-9]+)",
        r"/discovery/item/([a-fA-F0-9]+)",
        r"/search_result/([a-fA-F0-9]+)",
        r"/user/profile/[a-fA-F0-9]+/([a-fA-F0-9]+)",
    )
    m = None
    for pattern in patterns:
        m = re.search(pattern, path)
        if m:
            break

    if not m:
        raise ValueError(f"无法从 URL 提取 note_id: {url}")

    note_id = m.group(1)

    # xsec_token can appear in query params or the path fragment
    xsec = query.get("xsec_token", [None])[0]

    return {"note_id": note_id, "xsec_token": xsec or ""}


# ---------------------------------------------------------------------------
# Backend-specific metadata fetching
# ---------------------------------------------------------------------------


def _fetch_note_metadata(
    url: str, note_id: str, xsec_token: str, backend: str
) -> dict:
    """Fetch note metadata from the active backend."""
    if backend.startswith("OpenCLI"):
        return _fetch_opencli(url)
    elif backend.startswith("xiaohongshu-mcp"):
        return _fetch_mcp(note_id, xsec_token)
    else:
        return _fetch_xhs_cli(url)


def _is_opencli_fieldlist(data) -> bool:
    """True if `data` is OpenCLI's `[{field, value}, ...]` note output."""
    return (
        isinstance(data, list)
        and len(data) > 0
        and all(isinstance(x, dict) and "field" in x for x in data)
    )


def _coerce_count(val):
    """Coerce XHS count strings ('3730', '2.3万') to int when possible."""
    if isinstance(val, int):
        return val
    if not isinstance(val, str):
        return val
    s = val.strip()
    if not s:
        return val
    if "万" in s:
        try:
            return int(float(s.replace("万", "").strip()) * 10000)
        except ValueError:
            return val
    try:
        return int(s)
    except ValueError:
        return val


def _split_tags(val) -> list:
    """Split an XHS tag string ('#a, #b') or list into a clean list."""
    if isinstance(val, list):
        return [str(t).strip() for t in val if str(t).strip()]
    if isinstance(val, str):
        return [t.strip() for t in re.split(r"[,，]", val) if t.strip()]
    return []


def _opencli_fieldlist_to_note(rows: list, url: str) -> dict:
    """Convert OpenCLI `note -f yaml` field/value rows into the note schema.

    OpenCLI's note output only carries metadata (no media URLs and no note
    type/id), so images/videos are left empty and fetched via the native
    `opencli download` fallback in :func:`download_xhs_note`.
    """
    flat = {}
    for row in rows:
        if isinstance(row, dict) and "field" in row:
            flat[row["field"]] = row.get("value")

    note_id = _parse_xhs_url(url)["note_id"]
    return {
        "note_id": note_id,
        "title": flat.get("title", "") or "",
        "desc": flat.get("content", "") or flat.get("desc", "") or "",
        "type": flat.get("type", "") or "",
        "user": {"nickname": flat.get("author", "") or ""},
        "liked_count": _coerce_count(flat.get("likes")),
        "collected_count": _coerce_count(flat.get("collects")),
        "comment_count": _coerce_count(flat.get("comments")),
        "share_count": _coerce_count(flat.get("shares")),
        "tags": _split_tags(flat.get("tags")),
        "images": [],
    }


def _fetch_opencli(url: str, timeout: int = 60) -> dict:
    """Fetch note via OpenCLI (desktop, browser session)."""
    logger.info(f"Running: opencli xiaohongshu note \"{url[:60]}...\" -f yaml")
    try:
        proc = subprocess.run(
            ["opencli", "xiaohongshu", "note", url, "-f", "yaml"],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except FileNotFoundError:
        raise RuntimeError("opencli 未安装。运行: npm install -g @jackwener/opencli")

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:300]
        raise RuntimeError(f"opencli 调用失败 (exit {proc.returncode}): {stderr}")

    raw = proc.stdout.strip()
    if not raw:
        raise ValueError("opencli 返回了空输出")

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"opencli YAML 解析失败: {exc}")

    # OpenCLI `note -f yaml` returns a field/value list (title, author,
    # content, likes, ...) rather than a note object, and it carries no media
    # URLs. Normalize it into the note schema; media is fetched separately.
    if _is_opencli_fieldlist(data):
        return _opencli_fieldlist_to_note(data, url)

    if isinstance(data, list):
        # Search-result style list — take the first note.
        from agent_reach.channels.xiaohongshu import format_xhs_result
        return _ensure_dict(format_xhs_result(data), "opencli")

    if not isinstance(data, dict):
        raise ValueError(f"opencli 输出格式异常: {type(data).__name__}")

    from agent_reach.channels.xiaohongshu import format_xhs_result
    result = format_xhs_result(data)
    return _ensure_dict(result, "opencli")


def _fetch_mcp(note_id: str, xsec_token: str, timeout: int = 120) -> dict:
    """Fetch note via xiaohongshu-mcp (server, headless browser)."""
    call = f'xiaohongshu.get_feed_detail(feed_id: "{note_id}", xsec_token: "{xsec_token}")'
    logger.info(f"Running: mcporter call '{call}' --timeout {timeout * 1000}")

    try:
        proc = subprocess.run(
            ["mcporter", "call", call, "--timeout", str(timeout * 1000)],
            capture_output=True, text=True, timeout=timeout + 10,
            env={**__import__("os").environ, "PYTHONUTF8": "1"},
        )
    except FileNotFoundError:
        raise RuntimeError("mcporter 未安装。运行: npm install -g mcporter")

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:300]
        raise RuntimeError(
            f"mcporter 调用失败 (exit {proc.returncode}): {stderr}"
        )

    raw = proc.stdout.strip()
    if not raw:
        raise ValueError("xiaohongshu-mcp 返回了空输出")

    # mcporter may wrap the JSON in a "Result: {...}" line
    data = _parse_mcporter_output(raw)

    from agent_reach.channels.xiaohongshu import format_xhs_result
    result = format_xhs_result(data)
    return _ensure_dict(result, "xiaohongshu-mcp")


def _fetch_xhs_cli(url: str, timeout: int = 30) -> dict:
    """Fetch note via xhs-cli (legacy, unmaintained since 2026-03)."""
    logger.info(f"Running: xhs read {url[:60]}...")
    try:
        proc = subprocess.run(
            ["xhs", "read", url],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except FileNotFoundError:
        raise RuntimeError("xhs-cli 未安装。桌面用户建议安装 OpenCLI。")

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:300]
        raise RuntimeError(f"xhs-cli 调用失败 (exit {proc.returncode}): {stderr}")

    raw = proc.stdout.strip()
    if not raw:
        raise ValueError("xhs-cli 返回了空输出")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Some xhs-cli versions output YAML or text
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValueError(f"xhs-cli 输出解析失败: {exc}")

    if not isinstance(data, dict):
        raise ValueError(f"xhs-cli 输出格式异常: {type(data).__name__}")

    from agent_reach.channels.xiaohongshu import format_xhs_result
    result = format_xhs_result(data)
    return _ensure_dict(result, "xhs-cli")


# ---------------------------------------------------------------------------
# Mcporter output parser
# ---------------------------------------------------------------------------


def _ensure_dict(result, backend: str) -> dict:
    """Ensure format_xhs_result output is a single dict.

    For single-note fetches the result should be a dict, but the formatter
    may return a list (for search results).  Take the first item if it's a
    list, or wrap a non-dict scalar.
    """
    if isinstance(result, dict):
        return result
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            return first
        return {"note": first}
    if isinstance(result, list) and not result:
        raise ValueError(
            f"{backend} 返回了空列表（笔记可能不存在或需要登录）"
        )
    return {"note": result}


def _parse_mcporter_output(raw: str) -> dict:
    """Parse mcporter stdout, handling both bare JSON and wrapped formats.

    Known formats:
        {"key": "value", ...}
        Result: {"key": "value", ...}
        Text before {"key": ...} more text
    """
    # Try bare JSON first
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object in the output
    for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL):
        try:
            candidate = m.group(0)
            data = json.loads(candidate)
            if isinstance(data, dict) and data:
                return data
        except json.JSONDecodeError:
            continue

    raise ValueError(f"无法从 mcporter 输出中解析 JSON: {raw[:200]}")


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------


def _download_file(
    url: str,
    dest: Path,
    timeout: int = 30,
    retries: int = 3,
    backoff: float = 2.0,
) -> bool:
    """Download a file with retries and backoff.

    Returns True on success, False after exhausting retries.
    Non-recoverable HTTP errors (403, 404) are NOT retried.
    """
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                stream=True,
            )
            resp.raise_for_status()

            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (403, 404):
                logger.warning(f"  HTTP {status} for {url[:80]} (non-retryable)")
                return False
            logger.warning(
                f"  Attempt {attempt}/{retries}: HTTP {status} for {url[:80]}"
            )

        except requests.RequestException as exc:
            logger.warning(
                f"  Attempt {attempt}/{retries}: {exc} for {url[:80]}"
            )

        if attempt < retries:
            time.sleep(backoff * (2 ** (attempt - 1)))

    return False


def _download_media_opencli(url: str, note_dir: Path, timeout: int = 300):
    """Fetch images/videos via OpenCLI's native download command.

    OpenCLI's `note` returns only metadata (no media URLs), so on the OpenCLI
    backend we delegate media retrieval to `opencli xiaohongshu download` and
    move the resulting files into the standard ``images/`` and ``videos/``
    layout. Returns ``(image_paths, video_paths)`` relative to ``note_dir``.
    """
    img_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    vid_ext = {".mp4", ".mov", ".webm", ".m4v"}
    img_dir = make_private_dir(note_dir / "images")
    vid_dir = make_private_dir(note_dir / "videos")
    img_names: list = []
    vid_names: list = []

    with tempfile.TemporaryDirectory() as tmp:
        logger.info(
            f"Running: opencli xiaohongshu download \"{url[:60]}...\" --output {tmp}"
        )
        try:
            proc = subprocess.run(
                ["opencli", "xiaohongshu", "download", url, "--output", tmp, "-f", "yaml"],
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
        except FileNotFoundError:
            logger.warning("opencli 未安装，跳过媒体下载")
            return [], []
        except subprocess.TimeoutExpired:
            logger.warning("opencli download 超时，跳过媒体下载")
            return [], []

        if proc.returncode != 0:
            logger.warning(
                f"opencli download 失败 (exit {proc.returncode}): "
                f"{(proc.stderr or '')[:200]}"
            )
            return [], []

        for p in sorted(
            (f for f in Path(tmp).rglob("*") if f.is_file()),
            key=lambda x: x.name,
        ):
            ext = p.suffix.lower()
            if ext in vid_ext:
                name = f"{len(vid_names)}{ext}"
                shutil.move(str(p), str(vid_dir / name))
                vid_names.append(f"videos/{name}")
            elif ext in img_ext:
                name = f"{len(img_names)}{ext}"
                shutil.move(str(p), str(img_dir / name))
                img_names.append(f"images/{name}")
            else:
                logger.info(f"  跳过未知类型文件: {p.name}")

    logger.info(
        f"  opencli download: {len(img_names)} images, {len(vid_names)} videos"
    )
    return img_names, vid_names


def _patch_metadata_local_media(note_dir: Path) -> None:
    """Reconcile metadata.json media fields with files actually on disk.

    OpenCLI metadata has no media URLs, so we record the local file paths and
    infer the note type (video vs normal) from what was fetched. Scanning disk
    (rather than trusting a single fetch's return value) keeps metadata
    consistent even when a re-download's media fetch is rate-limited and the
    files from a prior run are still present.
    """
    metadata_path = note_dir / "metadata.json"
    if not metadata_path.exists():
        return
    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    def _list(sub: str) -> list:
        d = note_dir / sub
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file())

    img_files = _list("images")
    vid_files = _list("videos")
    if img_files:
        meta["images"] = [f"images/{n}" for n in img_files]
    if vid_files:
        meta["videos"] = [f"videos/{n}" for n in vid_files]
        if not meta.get("type"):
            meta["type"] = "video"
    elif img_files and not meta.get("type"):
        meta["type"] = "normal"

    metadata_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Metadata writer
# ---------------------------------------------------------------------------


def _write_metadata(note_dir: Path, note_data: dict, original_url: str) -> None:
    """Write metadata.json to the note directory."""
    metadata = {
        "note_id": note_data.get("note_id") or note_data.get("id", ""),
        "title": note_data.get("title", ""),
        "desc": note_data.get("desc") or note_data.get("content", ""),
        "type": note_data.get("type", ""),
        "author": note_data.get("user", {}),
        "stats": {
            k: note_data.get(k, 0)
            for k in ("liked_count", "collected_count", "comment_count", "share_count")
        },
        "tags": note_data.get("tags", []),
        "images": note_data.get("images", []),
        "original_url": original_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    metadata_path = note_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"  metadata.json written")


# ---------------------------------------------------------------------------
# Video URL extraction
# ---------------------------------------------------------------------------


def _extract_video_urls(note_data: dict) -> list:
    """Extract video URLs from note data (backend-dependent field names)."""
    urls = []

    # Check common field names
    for key in ("video_url", "video", "video_addr", "video_urls"):
        val = note_data.get(key)
        if isinstance(val, str) and val.startswith("http"):
            urls.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.startswith("http"):
                    urls.append(item)
                elif isinstance(item, dict):
                    u = item.get("url") or item.get("master_url") or ""
                    if u.startswith("http"):
                        urls.append(u)

    return urls


# ---------------------------------------------------------------------------
# Comments fetching
# ---------------------------------------------------------------------------


def _fetch_comments(
    url: str, note_id: str, xsec_token: str, backend: str
) -> list:
    """Fetch note comments from the active backend."""
    if backend.startswith("OpenCLI"):
        logger.info("Fetching comments via OpenCLI...")
        try:
            # Current OpenCLI requires a full signed URL (not a bare note_id).
            proc = subprocess.run(
                ["opencli", "xiaohongshu", "comments", url, "-f", "yaml"],
                capture_output=True, text=True, timeout=30,
                env={**__import__("os").environ, "PYTHONUTF8": "1"},
            )
            if proc.returncode != 0:
                logger.warning(f"opencli comments returned non-zero: {(proc.stderr or '')[:200]}")
                return []
            raw = proc.stdout.strip()
            if not raw:
                return []
            data = yaml.safe_load(raw)
            if isinstance(data, list):
                return data
            return [data] if isinstance(data, dict) else []
        except Exception as exc:
            logger.warning(f"Comments fetch via OpenCLI failed: {exc}")
            return []

    elif backend.startswith("xiaohongshu-mcp"):
        # MCP get_feed_detail already returns comments in the note data — the
        # caller should have extracted them.  This path is reached when the
        # caller explicitly asks for comments after the initial fetch.
        # We issue a second call.
        call = (
            f'xiaohongshu.get_feed_detail('
            f'feed_id: "{note_id}", xsec_token: "{xsec_token}")'
        )
        logger.info(f"Fetching comments via MCP: mcporter call '{call}'")
        try:
            proc = subprocess.run(
                ["mcporter", "call", call, "--timeout", "120000"],
                capture_output=True, text=True, timeout=130,
                env={**__import__("os").environ, "PYTHONUTF8": "1"},
            )
            if proc.returncode != 0:
                logger.warning(f"MCP comments call failed: {proc.stderr[:200]}")
                return []
            data = _parse_mcporter_output(proc.stdout.strip())
            comments = data.get("comments") or data.get("comment_list") or []
            return comments if isinstance(comments, list) else []
        except Exception as exc:
            logger.warning(f"Comments fetch via MCP failed: {exc}")
            return []

    else:
        # xhs-cli
        logger.info(f"Fetching comments via xhs-cli: xhs comments {note_id}")
        try:
            proc = subprocess.run(
                ["xhs", "comments", note_id],
                capture_output=True, text=True, timeout=30,
                env={**__import__("os").environ, "PYTHONUTF8": "1"},
            )
            if proc.returncode != 0:
                logger.warning(f"xhs-cli comments returned non-zero: {proc.stderr[:200]}")
                return []
            raw = proc.stdout.strip()
            if not raw:
                return []
            data = json.loads(raw)
            return data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        except Exception as exc:
            logger.warning(f"Comments fetch via xhs-cli failed: {exc}")
            return []
