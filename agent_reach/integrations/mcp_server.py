# -*- coding: utf-8 -*-
"""
Agent Reach MCP Server — expose internet platform tools via MCP protocol.

Run: python -m agent_reach.integrations.mcp_server

Currently supported platforms:
  - Bilibili: search, hot, video info, download (via bili-cli + bilix)
"""

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    Server = None  # type: ignore
    stdio_server = None  # type: ignore
    Tool = None  # type: ignore
    TextContent = None  # type: ignore


# ── helpers ────────────────────────────────────────────────────────────────

async def _run(*args: str, timeout: float = 60) -> Dict[str, Any]:
    """Run a command, return stdout/stderr/returncode."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode or 0,
            "stdout": stdout.decode("utf-8", errors="replace").strip(),
            "stderr": stderr.decode("utf-8", errors="replace").strip(),
        }
    except asyncio.TimeoutError:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"命令超时（{timeout}s）"}
    except FileNotFoundError:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"命令未找到: {args[0]}"}
    except Exception as e:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(e)}


def _parse_yaml_output(result: Dict[str, Any]) -> Dict[str, Any]:
    """Parse bili-cli / bilix YAML output into structured dict."""
    if not result["ok"]:
        return result

    stdout = result["stdout"]
    # bili-cli output: first line can be "ok: true" header
    # If stdout starts with "ok:", parse as root-level YAML
    try:
        data = yaml.safe_load(stdout)
        if isinstance(data, dict):
            return {"ok": True, "data": data.get("data", data)}
        return {"ok": True, "data": data}
    except yaml.YAMLError:
        # Plain text output (e.g. bilix info), return as raw text
        return {"ok": True, "data": stdout}


def _sanitize_path(path: str) -> Path:
    """Resolve and validate a download path — no path traversal."""
    p = Path(path).expanduser().resolve()
    # Require path to be under /tmp or ~/Downloads or cwd for safety
    allowed_roots = [
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve(),
        (Path.home() / "Downloads").resolve(),
        (Path.home() / "Desktop").resolve(),
        Path.cwd().resolve(),
    ]
    for root in allowed_roots:
        try:
            p.relative_to(root)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"下载目录不在允许的范围内: {path}")
    return p


# ── Bilibili tools ─────────────────────────────────────────────────────────

async def bilibili_search(query: str, n: int = 10, type: str = "video") -> Dict[str, Any]:
    """Search Bilibili videos via bili-cli."""
    result = await _run("bili", "search", query, "--type", type, "-n", str(n))
    return _parse_yaml_output(result)


async def bilibili_hot(n: int = 10) -> Dict[str, Any]:
    """Get Bilibili trending videos via bili-cli."""
    result = await _run("bili", "hot", "-n", str(n))
    return _parse_yaml_output(result)


async def bilibili_video_info(bvid: str) -> Dict[str, Any]:
    """Get Bilibili video details via bili-cli."""
    result = await _run("bili", "video", bvid)
    return _parse_yaml_output(result)


async def bilibili_download(
    url: str,
    dir: Optional[str] = None,
    quality: int = 0,
    only_audio: bool = False,
    subtitle: bool = False,
    dm: bool = False,
    image: bool = False,
) -> Dict[str, Any]:
    """Download a Bilibili video via bilix.

    Args:
        url: Bilibili video URL (e.g. https://www.bilibili.com/video/BVxxx)
        dir: Download directory (default: system temp dir)
        quality: 0 = highest available without login, higher = lower quality
        only_audio: Download audio only
        subtitle: Also download subtitles
        dm: Also download danmaku (弹幕)
        image: Also download cover image
    """
    download_dir = str(_sanitize_path(dir or tempfile.mkdtemp(prefix="bilix_")))

    args = ["bilix", "get_video", url, "--dir", download_dir, "-q", str(quality)]
    if only_audio:
        args.append("--only-audio")
    if subtitle:
        args.append("--subtitle")
    if dm:
        args.append("--dm")
    if image:
        args.append("--image")
    # Don't download entire series — just the single video
    args.append("--no-series")

    result = await _run(*args, timeout=600)  # 10 min for larger files

    if result["ok"]:
        # List downloaded files
        files = []
        d = Path(download_dir)
        if d.exists():
            for f in sorted(d.iterdir()):
                if f.is_file():
                    files.append({"name": f.name, "size": f.stat().st_size, "path": str(f)})

        return {
            "ok": True,
            "dir": download_dir,
            "files": files,
            "message": result["stdout"] or result["stderr"],
        }
    else:
        return result


# ── MCP server ─────────────────────────────────────────────────────────────

# Tool name → (handler, description, inputSchema)
TOOLS = {
    "bilibili_search": (
        bilibili_search,
        "搜索 B站视频或用户（通过 bili-cli，无需登录）。返回标题、作者、播放量、BV号等。",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "n": {"type": "integer", "description": "返回数量，默认 10", "default": 10},
                "type": {"type": "string", "enum": ["video", "user"], "description": "搜索类型", "default": "video"},
            },
            "required": ["query"],
        },
    ),
    "bilibili_hot": (
        bilibili_hot,
        "获取 B站当前热门视频（通过 bili-cli）。返回视频标题、UP主、播放量、弹幕数等。",
        {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "返回数量，默认 10", "default": 10},
            },
        },
    ),
    "bilibili_video_info": (
        bilibili_video_info,
        "获取 B站视频详情（通过 bili-cli）。返回标题、UP主、播放量、弹幕数、简介、字幕可用性等。",
        {
            "type": "object",
            "properties": {
                "bvid": {"type": "string", "description": "B站视频 BV 号，例如 BV1DfrdByE2H"},
            },
            "required": ["bvid"],
        },
    ),
    "bilibili_download": (
        bilibili_download,
        "下载 B站视频（通过 bilix）。可下载视频、音频、字幕、弹幕、封面。未登录最高 480P，登录后解锁 1080P+。",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "B站视频 URL，例如 https://www.bilibili.com/video/BVxxx"},
                "dir": {"type": "string", "description": "下载目录，默认系统临时目录"},
                "quality": {"type": "integer", "description": "画质：0=最高可用（默认），数值越大画质越低", "default": 0},
                "only_audio": {"type": "boolean", "description": "仅下载音频", "default": False},
                "subtitle": {"type": "boolean", "description": "同时下载字幕", "default": False},
                "dm": {"type": "boolean", "description": "同时下载弹幕", "default": False},
                "image": {"type": "boolean", "description": "同时下载封面图", "default": False},
            },
            "required": ["url"],
        },
    ),
}


def create_server():
    if not HAS_MCP:
        print("MCP not installed. Install: pip install mcp", file=sys.stderr)
        sys.exit(1)

    # Check critical dependencies
    missing = []
    for cmd in ["bili", "bilix"]:
        if shutil.which(cmd) is None:
            missing.append(cmd)

    server = Server("agent-reach")

    @server.list_tools()
    async def list_tools():
        tools = []
        for name, (_, description, schema) in TOOLS.items():
            tools.append(Tool(name=name, description=description, inputSchema=schema))
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        tool = TOOLS.get(name)
        if tool is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        handler, _, _ = tool
        try:
            result = await handler(**arguments)
            return [TextContent(
                type="text",
                text=json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2)
            )]
        except ValueError as e:
            return [TextContent(
                type="text",
                text=json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2)
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False, indent=2)
            )]

    if missing:
        print(f"⚠️  未找到: {', '.join(missing)} — 部分工具将不可用", file=sys.stderr)

    return server


async def main():
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
