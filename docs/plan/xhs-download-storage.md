# Plan: XHS Note Download/Storage

## Context

Add a `download` command for Xiaohongshu notes, storing images/videos/metadata locally in a MediaCrawler-inspired directory structure. Agent Reach currently only reads/searches XHS — this adds local archival capability.

## Target Storage Structure

```
~/.agent-reach/xiaohongshu/   (configurable via xhs_save_path)
└── {note_id}/
    ├── metadata.json          # title, author, stats, tags, image URLs, original URL
    ├── images/
    │   ├── 0.jpg
    │   └── ...
    ├── videos/
    │   └── 0.mp4
    └── comments.json          # optional
```

## Files to Create/Modify

### 1. NEW: `agent_reach/download.py` — core download engine

Public API: `download_xhs_note(url, save_path=None, config=None, download_media=True, download_comments=True) -> Path`

Flow:
1. Instantiate `XiaoHongShuChannel`, call `check(config)`, read `active_backend`
2. Parse URL → `note_id` + `xsec_token` (`_parse_xhs_url`)
3. Fetch metadata from active backend (`_fetch_note_opencli` / `_fetch_note_mcp` / `_fetch_note_xhs_cli`)
4. Clean output via `format_xhs_result()` (already exists in xiaohongshu.py)
5. Create save dir with `make_private_dir()` (0o700, from `utils/paths.py`)
6. Write `metadata.json`
7. Download images/videos (`_download_file` with requests + retry)
8. Optionally fetch comments → `comments.json`
9. Return `Path` to note directory

Key error handling: missing backend → `RuntimeError`; parse failure → `ValueError`; individual media download failure → warn + skip, never fail the whole download.

### 2. MODIFY: `agent_reach/cli.py`

Add subcommand:
```bash
agent-reach download xhs <URL> [--save-path PATH] [--no-media] [--no-comments]
```
Follow existing `_cmd_*` handler pattern (like `_cmd_transcribe`).

### 3. MODIFY: `agent_reach/channels/xiaohongshu.py`

Add `download_note()` convenience method on `XiaoHongShuChannel` that delegates to `download_xhs_note()`. Consistent with V2EXChannel's existing convenience methods.

### 4. NEW: `tests/test_download.py`

10+ tests covering: URL parsing, file download (success/retry/404), backend routing, metadata.json content, directory structure, CLI integration. Follow existing pytest + monkeypatch + unittest patterns.

### 5. MODIFY: `agent_reach/skill/references/social.md`

Document the download command in the XHS section.

## Design Rationale

- **Separate `download.py`** (not in channel): follows `transcribe.py` pattern; channels stay pure health-checkers
- **`requests` for HTTP downloads**: already a dependency; upstream tools don't download media files
- **`yaml.safe_load()` for OpenCLI output**: `pyyaml` already a dependency; matches `-f yaml` flag in docs
- **Media URL expiration handling**: skip + warn on 403/404, never fail the whole download
- **No parallel downloads**: keep it simple; single-threaded synchronous for now

## Verification

1. `pytest tests/test_download.py -v` — all unit tests pass
2. `pytest tests/ -v` — no regressions in existing tests
3. `python -m agent_reach.cli download xhs --help` — CLI help renders correctly
4. With a real XHS backend: `agent-reach download xhs "https://www.xiaohongshu.com/explore/..."` — creates expected directory structure with images + metadata.json
