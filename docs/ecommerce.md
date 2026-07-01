# 电商平台配置指南

Agent-Reach 支持 **14 个国内外电商平台**，通过 `ecommerce-cli`（基于 Playwright 的浏览器自动化工具）实现商品搜索、详情抓取、评论获取和跨平台比价。

## 前提条件

```bash
# 1. 安装 ecommerce-cli
pipx install ecommerce-cli

# 2. 安装 Chromium 浏览器
python -m playwright install chromium
```

## 支持的平台

### 国内电商

| 平台 | Channel | 配置要求 | 能力 |
|---|---|---|---|
| 淘宝 | `taobao` | 登录 Cookie | 搜索、详情、评论 |
| 天猫 | `tmall` | 登录 Cookie | 搜索、详情、评论 |
| 京东 | `jd` | 登录 Cookie（详情需要） | 搜索、详情、评论、价格历史 |
| 拼多多 | `pinduoduo` | 登录 Cookie（反爬极强） | 搜索、详情 |
| 闲鱼 | `goofish` | 登录 Cookie | 搜索、详情 |

### 国外电商

| 平台 | Channel | 配置要求 | 能力 |
|---|---|---|---|
| Amazon | `amazon` | 建议配置 Cookie | 搜索、详情、评论、价格历史 |
| eBay | `ebay` | 无需登录 | 搜索、详情 |
| Walmart | `walmart` | 无需登录 | 搜索、详情 |
| Best Buy | `bestbuy` | 无需登录 | 搜索、详情、评论 |
| Target | `target` | 无需登录 | 搜索、详情 |
| Etsy | `etsy` | 无需登录 | 搜索、详情 |
| AliExpress | `aliexpress` | 无需登录 | 搜索、详情 |
| Shopee | `shopee` | 无需登录 | 搜索、详情 |
| Lazada | `lazada` | 无需登录 | 搜索、详情 |

## 快速配置

### 方式一：一键提取（本地浏览器）

如果你的电脑上已经用 Chrome 登录了各个电商平台：

```bash
agent-reach configure --from-browser chrome
```

这会自动提取所有已登录平台的 Cookie，包括淘宝、京东、Amazon 等，并自动配置到 `ecommerce-cli`。

### 方式二：手动配置单个平台

```bash
# 从 Chrome 提取单个平台的 Cookie
ecommerce-cli jd configure --from-browser chrome

# 或手动粘贴 Cookie 字符串
ecommerce-cli taobao configure --cookie "key1=value1; key2=value2; ..."
```

### 方式三：Cookie-Editor 导出（服务器用户）

如果 Agent 运行在服务器上，可以使用 Cookie-Editor 扩展：

1. 在本地 Chrome 中安装 [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)
2. 访问电商网站（如 `taobao.com`）并确保已登录
3. 点击 Cookie-Editor 图标 → **Export** → **Header String**
4. 将结果粘贴给 Agent，Agent 会执行：
   ```bash
   ecommerce-cli taobao configure --cookie "<粘贴的 Cookie 字符串>"
   ```

## 使用方式

配置完成后，Agent 可以直接调用 `ecommerce-cli`：

### 搜索商品

```bash
ecommerce-cli jd search "机械键盘" -n 10 --min-price 100 --max-price 500
ecommerce-cli amazon search "laptop" -n 10 --sort price_asc
```

### 读取商品详情

```bash
ecommerce-cli taobao read "https://item.taobao.com/item.htm?id=123456"
ecommerce-cli amazon read "https://www.amazon.com/dp/B09XYZ"
```

### 获取评论

```bash
ecommerce-cli jd reviews "https://item.jd.com/100012345.html" --pages 3
ecommerce-cli amazon reviews "https://www.amazon.com/dp/B09XYZ" --pages 5
```

### 价格历史

```bash
ecommerce-cli jd price-history "https://item.jd.com/100012345.html" --period 3m
ecommerce-cli amazon price-history "https://www.amazon.com/dp/B09XYZ" --period 6m
```

### 跨平台比价

```bash
# 比较同一商品在不同平台的价格
ecommerce-cli compare "iPhone 16 Pro 256GB" --platforms amazon,ebay,bestbuy,walmart

# 按 URL 找同款
ecommerce-cli compare-by-url "https://item.jd.com/100012345.html"
```

### 健康检查

```bash
ecommerce-cli jd check
ecommerce-cli amazon check
```

## 输出格式

所有命令输出统一 JSON 格式，方便 Agent 解析：

### 搜索结果示例

```json
{
  "platform": "jd",
  "query": "机械键盘",
  "total": 5,
  "items": [
    {
      "title": "罗技 G Pro X 机械键盘",
      "price": 899.00,
      "currency": "CNY",
      "url": "https://item.jd.com/100012345.html",
      "image": "https://img.jd.com/...",
      "shop": "罗技官方旗舰店",
      "platform": "jd"
    }
  ]
}
```

### 商品详情示例

```json
{
  "url": "https://item.jd.com/100012345.html",
  "platform": "jd",
  "title": "罗技 G Pro X 机械键盘",
  "price": 899.00,
  "currency": "CNY",
  "seller": "罗技官方旗舰店",
  "in_stock": true,
  "description": "专业级电竞机械键盘，GX Blue 青轴...",
  "images": ["https://img.jd.com/...", "https://img.jd.com/..."]
}
```

### 比价结果示例

```json
{
  "query": "iPhone 16 Pro 256GB",
  "results": [
    {"platform": "amazon", "items": [{"title": "...", "price": 1099.00}]},
    {"platform": "bestbuy", "items": [{"title": "...", "price": 999.00}]}
  ],
  "best_price": {"platform": "bestbuy", "price": 999.00, "title": "..."}
}
```

## 常见问题

### 淘宝/天猫需要登录怎么办？

淘宝和天猫的反爬机制较强，需要登录 Cookie。确保：

1. 在 Chrome 中登录 `taobao.com` 和 `tmall.com`
2. 关闭 Chrome（重要！否则 rookiepy 无法读取 Cookie）
3. 运行 `ecommerce-cli taobao configure --from-browser chrome`

### 拼多多搜索失败？

拼多多是反爬最强的电商平台，Web 端功能非常有限。建议：

- 使用移动端域名 `mobile.yangkeduo.com`
- 确保在浏览器中登录状态有效
- 可能需要住宅代理

### Amazon 出现验证码？

Amazon 会检测自动化访问。解决方法：

- 配置登录 Cookie：`ecommerce-cli amazon configure --from-browser chrome`
- 使用代理
- 减少请求频率

### 国内访问国外电商站点超时？

中国大陆访问 Best Buy、Target 等美国站点可能很慢或被阻断。使用代理：

```bash
ecommerce-cli bestbuy search "laptop" --proxy "http://host:port"
```

在 Agent-Reach 中配置持久代理：

```bash
agent-reach config set ecommerce_proxy "http://host:port"
```

### 如何查看当前配置状态？

```bash
agent-reach doctor
```

这会显示所有 27 个 Channel 的状态，包括 14 个电商 Channel。

## 架构说明

```
Agent (Claude Code 等)
  │ 直接调用 ecommerce-cli
  ▼
ecommerce-cli
  │ Playwright + Stealth + Cookie 注入
  ▼
电商平台 (淘宝/京东/Amazon/...)
```

Agent-Reach 的电商 Channel 只做可用性检查（`ecommerce-cli <platform> check`），不包装业务调用。Agent 直接调用 `ecommerce-cli search/read/reviews` 等子命令——就像 Agent 直接调用 `twitter search` 一样。

`ecommerce-cli` 是一个独立的 CLI 工具，可以脱离 Agent-Reach 单独使用。
