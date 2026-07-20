# /scrape-prompthero — 爬取 PromptHero 全站 Prompt + 图片

利用 Agent Reach 的 `prompthero` channel，全量爬取 https://prompthero.com/ 的所有图片及其对应 Prompt、模型参数，输出为 JSONL 格式（兼容 MediaCrawler 的 xhs JSONL 结构）。

**内置自动 checkpoint（断点续传）**：程序中断后重新运行自动从上次进度恢复，无需手动干预。

## 使用示例

```bash
# 全量爬取（默认浏览器滚动 + checkpoint）
/scrape-prompthero

# 指定输出目录
/scrape-prompthero --output ./prompthero_data

# 只爬前 100 个 prompt（测试/采样）
/scrape-prompthero --max-prompts 100

# 不下载图片（仅保存 JSONL 中的 image_url）
/scrape-prompthero --no-images

# 加快/放慢请求间隔（默认 1 秒）
/scrape-prompthero --delay 0.5

# 关闭 checkpoint（从头开始，不保留进度）
/scrape-prompthero --no-checkpoint

# 调整 checkpoint 保存频率（默认每 10 条保存一次）
/scrape-prompthero --checkpoint-every 50

# 调整滚动参数
/scrape-prompthero --scroll-timeout 300 --scroll-pause 2.0

# 探测并打印 HTML 结构（用于适配选择器）
/scrape-prompthero --probe

# 从单个 URL 读取并打印结构化数据（调试用）
/scrape-prompthero --url https://prompthero.com/prompt/627d4f5a2b5
```

## 参数

`$ARGUMENTS` 解析：
- `--output {dir}`：输出目录，默认 `data/prompthero/`
- `--max-prompts N`：最多爬取 N 个 prompt（默认 None = 全部）
- `--no-images`：跳过图片下载，仅保留 `image_url`
- `--delay N`：详情页请求间隔秒数（默认 1.0）
- `--no-checkpoint`：禁用自动 checkpoint / 断点续传
- `--checkpoint-every N`：每 N 条记录保存一次 checkpoint（默认 10）
- `--scroll-timeout N`：浏览器滚动超时秒数（默认 120）
- `--scroll-pause N`：每次滚动后等待秒数（默认 1.5）
- `--probe`：抓取首页和一个详情页，打印 HTML 结构片段和检测到的选择器匹配情况，用于调试
- `--url {url}`：仅读取单个 prompt URL，打印 JSON 后退出
- 其他文本：视为输出目录路径（兼容 `/scrape-prompthero ./mydata`）

---

## 第一步：环境检查

### 1a. 依赖检查

```python
import importlib.util

def check_dep(name, pkg):
    ok = importlib.util.find_spec(name) is not None
    print(f"  {'✅' if ok else '❌'} {pkg}")
    return ok

has_bs4 = check_dep("bs4", "beautifulsoup4")
has_requests = check_dep("requests", "requests (optional)")

if not has_bs4:
    print("\n[!] beautifulsoup4 未安装，执行：pip install beautifulsoup4")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "beautifulsoup4"])
```

### 1b. 网络连通性检查

```python
import urllib.request
req = urllib.request.Request("https://prompthero.com/", headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"✅ 网络连通（HTTP {resp.status}）")
except Exception as e:
    print(f"❌ 无法连接 prompthero.com: {e}")
    print("提示：如需代理，先执行 agent-reach configure proxy http://...")
    raise SystemExit(1)
```

---

## 第二步：HTML 结构探测（`--probe` 时执行）

当 `$ARGUMENTS` 包含 `--probe` 时，抓取首页和第一个详情页，输出关键 HTML 片段，帮助确认 CSS 选择器是否需要调整。

```python
#!/usr/bin/env python3
"""Probe PromptHero HTML structure."""
import urllib.request
from bs4 import BeautifulSoup

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

# 1. List page
print("=== List Page (https://prompthero.com/) ===\n")
html = fetch("https://prompthero.com/")
soup = BeautifulSoup(html, "html.parser")

# Find prompt links
links = soup.select('a[href^="/prompt/"]')
print(f"Found {len(links)} links matching 'a[href^=\"/prompt/\"]'")
for a in links[:5]:
    print(f"  href={a.get('href')}  text={a.get_text(strip=True)[:60]}")

if not links:
    print("  No links found. Dumping first <a> tags:")
    for a in soup.find_all("a")[:10]:
        print(f"    href={a.get('href')}  text={a.get_text(strip=True)[:60]}")

# 2. Detail page (first link)
if links:
    first = links[0].get("href", "")
    if first.startswith("/"):
        first = "https://prompthero.com" + first
    print(f"\n=== Detail Page ({first}) ===\n")
    dhtml = fetch(first)
    dsoup = BeautifulSoup(dhtml, "html.parser")

    # Try to find prompt text with common selectors
    selectors = [
        'div.prompt-text', '.prompt-content', 'pre.prompt',
        '[data-testid="prompt-text"]', '#prompt-text',
        '.prompt', 'article', 'main',
    ]
    for sel in selectors:
        el = dsoup.select_one(sel)
        if el:
            print(f"✅ Selector '{sel}' matched:")
            print(f"   text={el.get_text(strip=True)[:200]}")
            break
    else:
        print("❌ No prompt text found with known selectors.")
        print("   Dumping first 500 chars of body text:")
        body = dsoup.find("body")
        if body:
            print(body.get_text(separator="\n", strip=True)[:500])

    # OG image
    og = dsoup.select_one('meta[property="og:image"]')
    if og:
        print(f"\n✅ OG Image: {og.get('content')}")
    else:
        print("\n❌ No og:image meta found.")
```

**Agent 执行步骤**：
1. 保存并运行上述探测脚本
2. 根据输出确认选择器是否命中
3. 如果选择器未命中，更新 `agent_reach/channels/prompthero.py` 中的 `SELECTORS` 字典
4. 重新运行 `/scrape-prompthero`

---

## 第三步：执行爬取

### 3a. 解析参数

```python
#!/usr/bin/env python3
"""Parse arguments from $ARGUMENTS string."""
import sys

args = sys.argv[1:] if len(sys.argv) > 1 else []

# Defaults
output_dir = "data/prompthero"
max_prompts = None
download_images = True
delay = 1.0
probe_mode = False
single_url = None
checkpoint = True
checkpoint_every = 10
scroll_timeout = 120.0
scroll_pause = 1.5

i = 0
while i < len(args):
    a = args[i]
    if a == "--output" and i + 1 < len(args):
        output_dir = args[i + 1]
        i += 2
    elif a == "--max-prompts" and i + 1 < len(args):
        max_prompts = int(args[i + 1])
        i += 2
    elif a == "--no-images":
        download_images = False
        i += 1
    elif a == "--delay" and i + 1 < len(args):
        delay = float(args[i + 1])
        i += 2
    elif a == "--no-checkpoint":
        checkpoint = False
        i += 1
    elif a == "--checkpoint-every" and i + 1 < len(args):
        checkpoint_every = int(args[i + 1])
        i += 2
    elif a == "--scroll-timeout" and i + 1 < len(args):
        scroll_timeout = float(args[i + 1])
        i += 2
    elif a == "--scroll-pause" and i + 1 < len(args):
        scroll_pause = float(args[i + 1])
        i += 2
    elif a == "--probe":
        probe_mode = True
        i += 1
    elif a == "--url" and i + 1 < len(args):
        single_url = args[i + 1]
        i += 2
    elif a.startswith("-"):
        print(f"Unknown flag: {a}")
        i += 1
    else:
        # Positional arg = output dir
        output_dir = a
        i += 1
```

### 3b. 调用 Agent Reach PromptheroChannel

```python
#!/usr/bin/env python3
"""Run the PromptHero scraper via Agent Reach channel."""
import json
import sys
from pathlib import Path

# Ensure project root is importable
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from agent_reach.channels.prompthero import PromptheroChannel

channel = PromptheroChannel()

# Single URL mode
if single_url:
    data = channel.read(single_url)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(0)

# Probe mode handled in Step 2
if probe_mode:
    print("Probe mode: run the probe script from Step 2, then exit.")
    sys.exit(0)

# Full scrape
jsonl_path = channel.scrape_all(
    output_dir=output_dir,
    max_prompts=max_prompts,
    delay=delay,
    download_images=download_images,
    checkpoint=checkpoint,
    checkpoint_every=checkpoint_every,
    scroll_timeout=scroll_timeout,
    scroll_pause=scroll_pause,
)
print(f"\n✅ JSONL saved: {jsonl_path}")
```

---

## 第四步：输出格式说明

生成的 JSONL 文件每行一个 JSON 对象，字段设计兼容 MediaCrawler xhs JSONL 风格：

| 字段 | 类型 | 说明 |
|------|------|------|
| `prompt_id` | str | Prompt 唯一 ID（从 URL 提取） |
| `prompt_url` | str | 详情页完整 URL |
| `title` | str | 页面标题（如有） |
| `prompt_text` | str | 主 Prompt 文本 |
| `negative_prompt` | str | Negative prompt（如有） |
| `model` | str | 使用的 AI 模型（Stable Diffusion / Midjourney / DALL-E 等） |
| `sampler` | str | 采样器名称（如有） |
| `cfg_scale` | float | CFG Scale（如有） |
| `steps` | int | 生成步数（如有） |
| `seed` | int | 随机种子（如有） |
| `size` | str | 图片尺寸（如有） |
| `image_url` | str | 图片原始 URL |
| `image_local_path` | str | 本地保存的相对路径（如 `--no-images` 则无此字段） |
| `creator` | str | 创作者用户名（如有） |
| `creator_url` | str | 创作者主页 URL（如有） |
| `tags` | list[str] | 标签列表（如有） |
| `liked_count` | int | 点赞数（如有） |
| `scraped_at` | int | 爬取时间戳（毫秒） |

**示例 JSONL 行**：
```json
{
  "prompt_id": "627d4f5a2b5",
  "prompt_url": "https://prompthero.com/prompt/627d4f5a2b5",
  "title": "A beautiful fantasy landscape",
  "prompt_text": "masterpiece, best quality, fantasy landscape...",
  "negative_prompt": "low quality, blurry, ugly",
  "model": "Stable Diffusion 1.5",
  "sampler": "DPM++ 2M Karras",
  "cfg_scale": 7,
  "steps": 25,
  "seed": 123456789,
  "size": "512x768",
  "image_url": "https://prompthero.com/.../image.jpg",
  "image_local_path": "images/627d4f5a2b5.jpg",
  "creator": "artist_name",
  "creator_url": "https://prompthero.com/profile/artist_name",
  "tags": ["fantasy", "landscape", "anime"],
  "liked_count": 1250,
  "scraped_at": 1752988800000
}
```

---

## 第五步：Checkpoint / 断点续传

**默认开启**。程序在以下场景自动保存进度，下次运行自动恢复：

| 场景 | 行为 |
|------|------|
| `Ctrl+C` / `KeyboardInterrupt` | 立即保存 checkpoint，记录已爬取的 prompt_ids 和 scroll 状态 |
| 每 `--checkpoint-every N` 条记录 | 自动保存 checkpoint（默认每 10 条） |
| 程序崩溃 / 终端断开 | 下次运行自动恢复：若 scroll 已完成则跳过滚动直接爬详情页 |
| 无 checkpoint 但 JSONL 已存在 | 自动扫描 JSONL 重建 `seen_ids`，跳过已存在记录 |
| 爬取完成 | 自动删除 checkpoint 文件 |

### Checkpoint 文件

- 路径：`{output_dir}/.prompthero_checkpoint.json`
- 内容示例：
  ```json
  {
    "seen_ids": ["627d4f5a2b5", "627d4f5a2b6"],
    "total_scraped": 147,
    "scroll_completed": true,
    "updated_at": 1752988800
  }
  ```

### 恢复流程

```bash
# 第一次运行，爬了 1000 条后中断
/scrape-prompthero --output ./mydata
# ... Ctrl+C ...
# ⏸️  中断！checkpoint 已保存: total=1000, scroll_completed=true

# 重新运行同一命令，自动恢复
/scrape-prompthero --output ./mydata
# ▶️  从 checkpoint 恢复: 已爬取 1000 条, scroll_completed=true
```

### 强制从头开始

```bash
# 删除 checkpoint 和 JSONL，重新爬取
rm ./mydata/.prompthero_checkpoint.json ./mydata/prompthero_prompts_*.jsonl
/scrape-prompthero --output ./mydata

# 或使用 --no-checkpoint 禁用断点续传
/scrape-prompthero --output ./mydata --no-checkpoint
```

---

## 第六步：适配未知 HTML 结构

PromptHero 的页面结构可能随时间变化。如果 `--probe` 发现选择器未命中，按以下流程修复：

### 5a. 更新 CSS 选择器

编辑 `agent_reach/channels/prompthero.py` 顶部的 `SELECTORS` 字典：

```python
SELECTORS = {
    "list_prompt_links": 'a[href^="/prompt/"]',   # 列表页 prompt 链接
    "prompt_text": 'div.prompt-text, pre.prompt',   # 主 prompt 文本
    "negative_prompt": 'div.negative-prompt',       # negative prompt
    "model": 'div.model-name',                      # 模型名称
    "sampler": 'div.sampler',                       # 采样器
    "cfg_scale": 'div.cfg',                         # CFG scale
    "steps": 'div.steps',                           # 步数
    "seed": 'div.seed',                             # 种子
    "size": 'div.size',                             # 尺寸
    "creator": 'a[href^="/profile/"]',              # 创作者链接
    "tags": 'a.tag',                                # 标签
    "liked_count": 'span.likes',                    # 点赞数
    "title": 'h1',                                  # 标题
    "image": 'meta[property="og:image"]',          # 图片 URL
}
```

**规则**：多个选择器用逗号分隔，第一个匹配到的元素会被使用。

### 5b. 运行时覆盖（不修改源码）

也可以在 Python 中临时覆盖：

```python
channel = PromptheroChannel()
channel.selectors["prompt_text"] = 'div.new-prompt-class'
channel.scrape_all(output_dir="./data")
```

---

## 第七步：输出结果

爬取完成后向用户汇报：

```
=== PromptHero 爬取完成 ===

输出目录: {output_dir}/
JSONL 文件: {output_dir}/prompthero_prompts_YYYY-MM-DD.jsonl
图片目录: {output_dir}/images/

统计:
- 爬取 Prompt 数量: {N}
- 下载图片数量: {M}
- 请求失败数: {F}
- 总耗时: {T} 秒

文件结构:
{output_dir}/
├── prompthero_prompts_YYYY-MM-DD.jsonl   # 主数据文件
└── images/
    ├── abc123.jpg
    ├── def456.png
    └── ...

使用方式:
1. 用 pandas 读取 JSONL: pd.read_json(jsonl_path, lines=True)
2. 用 jq 过滤: jq 'select(.model == "Midjourney")' {jsonl_path}
3. 批量导入到数据库: mongoimport --db prompts --collection prompthero --file {jsonl_path}
```

---

## 第八步：错误处理

| 场景 | 处理 |
|------|------|
| `beautifulsoup4` 未安装 | 自动执行 `pip install beautifulsoup4` |
| 网络超时/连接失败 | 提示检查代理：`agent-reach configure proxy ...`；checkpoint 已保存，可安全重试 |
| 选择器未命中（字段为空） | 执行 `--probe` 模式，打印 HTML 结构供调试 |
| 图片下载失败 | 记录 WARN，保留 `image_url` 字段，继续爬取 |
| 单页读取失败 | 打印 ERROR，跳过该 prompt，继续下一页；checkpoint 不受影响 |
| 列表页重复（无新内容） | 自动停止爬取，避免无限循环 |
| 遇到反爬/429 | 增加 `--delay` 间隔，或暂停后继续；checkpoint 保证进度不丢失 |
| 磁盘空间不足 | 提前检查，提示清理或更换 `--output` 目录 |
| checkpoint 文件损坏 | 自动忽略，尝试从 JSONL 重建 `seen_ids` |
| 恢复后 JSONL 中有重复 | 通过 `seen_ids` 去重，不会写入重复记录 |
