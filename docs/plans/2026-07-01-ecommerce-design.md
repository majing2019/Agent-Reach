# 电商平台支持设计方案

> 日期：2026-07-01 | 状态：设计完成，待实施

## 1. 概述

为 Agent-Reach 新增 **14 个电商平台** 的读/搜索访问能力，覆盖国内外主流电商，支持商品搜索、详情抓取、评论获取和跨平台比价。

### 设计原则

- **遵循 Agent-Reach 胶水层哲学**：Agent-Reach 只做 Channel 可用性检查，实际抓取由独立上游 CLI 工具 (`ecommerce-cli`) 完成
- **Agent 直接调用上游工具**：就像 `twitter search "..."` 一样，Agent 直接调用 `ecommerce-cli <platform> search "..."`
- **浏览器自动化 + Cookie 注入**：所有平台通过 Playwright 模拟真实用户，Cookie 从本地浏览器一键提取

## 2. 支持的平台

### 国内电商（5 个）

| 平台 | Channel 名 | 搜索 | 详情 | 评论 | 价格历史 | 难度 |
|---|---|---|---|---|---|---|
| 淘宝 | `taobao` | ✅ | ✅ | ✅ | ❌ | 🔴 高 |
| 天猫 | `tmall` | ✅ | ✅ | ✅ | ❌ | 🔴 高（复用淘宝体系） |
| 京东 | `jd` | ✅ | ✅ | ✅ | ✅ | 🟡 中高 |
| 拼多多 | `pinduoduo` | ✅ | ✅ | ✅ | ❌ | 🔴 高 |
| 闲鱼 | `goofish` | ✅ | ✅ | ❌ | ❌ | 🟡 中 |

### 国外电商（9 个）

| 平台 | Channel 名 | 搜索 | 详情 | 评论 | 价格历史 | 难度 |
|---|---|---|---|---|---|---|
| Amazon | `amazon` | ✅ | ✅ | ✅ | ✅ (Keepa/Camel) | 🟡 中高 |
| eBay | `ebay` | ✅ | ✅ | ✅ | ❌ | 🟢 中 |
| Walmart | `walmart` | ✅ | ✅ | ✅ | ❌ | 🟢 中 |
| Best Buy | `bestbuy` | ✅ | ✅ | ✅ | ❌ | 🟢 中低 |
| Shopee | `shopee` | ✅ | ✅ | ✅ | ❌ | 🟡 中 |
| Lazada | `lazada` | ✅ | ✅ | ✅ | ❌ | 🟡 中 |
| AliExpress | `aliexpress` | ✅ | ✅ | ✅ | ❌ | 🟡 中 |
| Etsy | `etsy` | ✅ | ✅ | ✅ | ❌ | 🟢 中低 |
| Target | `target` | ✅ | ✅ | ✅ | ❌ | 🟢 中低 |

## 3. 总体架构

### 三层架构

```
Agent (Claude Code 等)
  │ 直接调用上游工具
  ▼
ecommerce-cli (独立 CLI 工具)
  │ Playwright + Cookie 注入 + 反检测
  ▼
电商平台 (淘宝/京东/Amazon/...)
```

### 与 Agent-Reach 的关系

- Agent-Reach 的 Channel **不调用** `ecommerce-cli` 做业务抓取
- Channel 只调用 `ecommerce-cli <platform> check` 做可用性检查
- Agent 直接调用 `ecommerce-cli` 做搜索和抓取
- 完全遵循现有模式（类比 `twitter-cli search "..."` → `ecommerce-cli taobao search "..."`）

## 4. `ecommerce-cli` 工具设计

### 4.1 命令行接口

```bash
# 搜索商品
ecommerce-cli <platform> search <query> [options]

# 读取商品详情
ecommerce-cli <platform> read <url>

# 抓取评论
ecommerce-cli <platform> reviews <url> [options]

# 价格历史（仅京东、Amazon）
ecommerce-cli <platform> price-history <url> [options]

# 跨平台比价
ecommerce-cli compare <query> --platforms <list>
ecommerce-cli compare-by-url <url>

# 健康检查
ecommerce-cli <platform> check

# 配置 Cookie
ecommerce-cli <platform> configure --cookie "<json>"
```

### 4.2 共享引擎

所有平台共享一套 Playwright 基础设施：

- **browser_pool**：Playwright 实例池，支持多平台并发
- **cookie_manager**：按平台/账号管理 Cookie（从浏览器导入或手动注入）
- **stealth_plugins**：反检测 — 隐藏 webdriver 痕迹、随机化浏览器指纹
- **proxy_manager**：可选代理配置（国内平台可能需要住宅代理）
- **rate_limiter**：请求频率控制，避免触发风控

### 4.3 配置目录结构

```
~/.ecommerce-cli/
├── profiles/
│   ├── taobao.json      # Playwright 持久化 profile
│   ├── jd.json
│   ├── amazon.json
│   └── ...
├── config.yaml           # 全局配置（代理、超时、默认浏览器路径）
└── stealth.js            # 反检测脚本
```

### 4.4 输出格式

统一 JSON 输出，方便 Agent 解析：

```json
{
  "platform": "jd",
  "query": "显卡",
  "total": 3421,
  "items": [
    {
      "title": "七彩虹 RTX 4070 Ti",
      "price": 4299.00,
      "currency": "CNY",
      "shop": "七彩虹官方旗舰店",
      "url": "https://item.jd.com/...",
      "image": "https://img.jd.com/...",
      "rating": 4.8,
      "reviews_count": 2341,
      "is_ad": false
    }
  ]
}
```

## 5. Channel 设计

### 5.1 统一模板

所有电商 Channel 继承相同结构：

```python
class TaobaoChannel(Channel):
    name = "taobao"
    description = "淘宝商品搜索与详情"
    backends = ["ecommerce-cli"]
    tier = 1  # 需要 Cookie 配置

    def can_handle(self, url):
        return "taobao.com" in url

    def check(self, config=None):
        # 1. 检查 ecommerce-cli 是否安装
        # 2. 检查 Playwright 浏览器是否就绪
        # 3. 检查 Cookie 是否已配置
        # 4. 快速冒烟测试
```

### 5.2 淘宝与天猫

- 天猫是淘宝体系内的 B2C 频道，共用 `taobao.com` 域名体系
- 两个独立 Channel，共享同一个 `ecommerce-cli` 后端
- `can_handle` 各自匹配 `taobao.com` 和 `tmall.com`
- 搜索时通过参数区分偏好（天猫偏品牌商品）

### 5.3 Channel 注册

在 `agent_reach/channels/__init__.py` 中新增 14 个 Channel 的导入和注册：

```python
from .taobao import TaobaoChannel
from .tmall import TmallChannel
from .jd import JdChannel
from .pinduoduo import PinduoduoChannel
from .goofish import GoofishChannel
from .amazon import AmazonChannel
from .ebay import EbayChannel
from .walmart import WalmartChannel
from .bestbuy import BestBuyChannel
from .shopee import ShopeeChannel
from .lazada import LazadaChannel
from .aliexpress import AliexpressChannel
from .etsy import EtsyChannel
from .target import TargetChannel
```

## 6. 认证与配置

### 6.1 三层 Cookie 加载策略

借鉴 Xueqiu Channel 的成熟模式：

1. **ecommerce-cli 自有 Cookie 存储** → `"ok"`
2. **从浏览器提取 Cookie → 注入 ecommerce-cli** → `"ok"`
3. **未配置** → `"warn"`（引导用户运行 `configure`）

### 6.2 配置流程

```bash
# 一键从本地 Chrome 提取所有已登录平台的 Cookie
agent-reach configure --from-browser chrome

# 触发各电商 Channel 的配置逻辑：
# 1. 从 Chrome 提取 taobao.com / jd.com / amazon.com 等域名的 Cookie
# 2. 调用 ecommerce-cli <platform> configure --cookie "<json>"
# 3. ecommerce-cli 保存到对应 profile，冒烟验证
```

### 6.3 配置存储

复用在 `~/.agent-reach/config.yaml` 中，新增键：

```yaml
# 电商平台配置
taobao_cookie: "..."       # 淘宝 Cookie 字符串
jd_cookie: "..."           # 京东 Cookie 字符串
amazon_cookie: "..."       # Amazon Cookie 字符串
ecommerce_proxy: "..."     # 可选代理（国内平台可能需要）
```

## 7. 跨平台比价设计

### 7.1 能力分级

| 级别 | 能力 | 平台 |
|---|---|---|
| L1 - 单平台比价 | 同一商品不同商家价格对比 | 淘宝、京东、Amazon、eBay |
| L2 - 跨平台同款 | 同一型号商品跨平台比价 | Amazon ↔ Best Buy ↔ Walmart ↔ Target（同 UPC/EAN） |
| L3 - 历史追踪 | 价格走势 + 降价提醒 | 京东、Amazon（Keepa/Camel） |

### 7.2 比价命令

```bash
# 按商品名称跨平台比价
ecommerce-cli compare "iPhone 16 Pro 256GB" --platforms amazon,ebay,bestbuy,walmart

# 按 URL 找同款
ecommerce-cli compare-by-url "https://item.jd.com/100012345.html"

# 查看价格历史
ecommerce-cli amazon price-history "https://www.amazon.com/dp/B09XYZ" --period 3m
```

### 7.3 匹配策略

- **国外平台**：优先 UPC/EAN/GTIN 码匹配，辅以标题相似度
- **国内平台**：无统一商品码，用标题 + 规格参数模糊匹配，标注匹配置信度
- **跨国内外**：商品型号体系不同，不做自动跨区比价

## 8. 实施计划

### 阶段 1：`ecommerce-cli` 独立项目

把 CLI 工具做出来，独立于 Agent-Reach，可单独使用和测试。

| 步骤 | 内容 |
|---|---|
| 1.1 | 搭建项目骨架：Python CLI（click），Playwright 引擎 |
| 1.2 | 实现反检测引擎：stealth 脚本、指纹随机化、请求节流 |
| 1.3 | 实现 Cookie 管理：浏览器导入（rookiepy）、Profile 持久化 |
| 1.4 | 先做一个简单平台验证（Best Buy — 反爬最弱） |
| 1.5 | 扩展到国外平台：Amazon、eBay、Walmart、Target、Etsy |
| 1.6 | 扩展到国内平台：京东、淘宝/天猫、拼多多、闲鱼 |
| 1.7 | 扩展到亚洲平台：Shopee、Lazada、AliExpress |
| 1.8 | 实现比价命令 |
| 1.9 | 发布到 PyPI：`pipx install ecommerce-cli` |

### 阶段 2：Agent-Reach 集成

CLI 工具稳定后，在 Agent-Reach 中注册所有 Channel。

| 步骤 | 内容 |
|---|---|
| 2.1 | 新增 14 个 Channel 文件（按模板） |
| 2.2 | 在 `__init__.py` 中注册 |
| 2.3 | 扩展 `configure --from-browser` 支持电商平台域名 Cookie 提取 |
| 2.4 | 更新 `config.py` 的 `FEATURE_REQUIREMENTS` |
| 2.5 | 更新 `mcporter.json`（如需要 MCP） |
| 2.6 | 编写测试 |

### 阶段 3：文档

| 步骤 | 内容 |
|---|---|
| 3.1 | README 更新：平台矩阵新增电商分类 |
| 3.2 | 新增 `docs/ecommerce.md`：电商平台配置指南 |
| 3.3 | SKILL.md / CLAUDE.md 更新 |

### 实施原则

- **先国外后国内**：国外反爬弱，先跑通全流程；国内平台反爬复杂，需要更多调优
- **`ecommerce-cli` 独立可交付**：阶段 1 完成后就可以独立使用，不依赖 Agent-Reach
- **一个平台一个 Channel**：保持现有代码组织模式，每个平台独立文件

## 9. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 国内平台反爬升级 | 搜索/抓取失败 | 维护 stealth 脚本更新；支持代理轮换 |
| 平台改版页面结构变更 | 选择器失效 | 每个平台维护选择器版本号；冒烟检测 |
| Cookie 过期频繁 | 用户体验差 | `check()` 检测 Cookie 有效性，及时提示重新配置 |
| Playwright 资源消耗大 | 性能问题 | 浏览器池复用；headless 模式；按需启动 |
| 法律/ToS 风险 | 合规问题 | 仅用于个人授权用途；文档标注风险提示 |
