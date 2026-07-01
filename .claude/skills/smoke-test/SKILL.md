---
name: smoke-test
description: Run a health check on all Agent Reach channels by searching for something on each platform.
triggers:
  - smoke test
  - health check
  - 冒烟测试
  - 体检
  - 检测爬虫
  - test all channels
  - check all crawlers
metadata:
  project: Agent-Reach
---

# Smoke Test — Agent Reach 全渠道冒烟测试

逐一对 Agent Reach 的所有 13 个渠道做真实的自然语言搜索/读取，验证每个爬虫是否真正可用。

## 执行规则

1. **并行执行**：所有渠道的测试同时发起，不要排队
2. **超时 15 秒**：单个渠道超过 15 秒没返回算失败
3. **失败不阻塞**：某个渠道挂不影响其他渠道
4. **如实报告**：最后用表格汇总所有结果

## 测试用例

### 零配置渠道

用以下自然语言分别测试，每个测试是一个独立的搜索/读取操作：

| # | 渠道 | 测试操作 | 验证标准 |
|---|------|---------|---------|
| 1 | Web | 读取网页 `https://example.com` | 返回内容包含 "Example Domain" |
| 2 | V2EX | 查看 V2EX 热门话题 | 返回至少一条话题 |
| 3 | RSS | 读取 `https://hnrss.org/frontpage` | 返回至少一篇文章 |
| 4 | Bilibili | 在 B 站搜索 "Python教程" | 返回至少一个视频 |
| 5 | Xueqiu | 查雪球茅台(SH600519)行情 | 返回股票名称和价格 |
| 6 | Exa Search | 全网搜索 "artificial intelligence" | 返回至少一条结果 |

### 需登录/配置的渠道

| # | 渠道 | 测试操作 | 验证标准 |
|---|------|---------|---------|
| 7 | GitHub | 搜索 GitHub 仓库 "awesome" | 返回至少一个仓库 |
| 8 | YouTube | 搜索 YouTube 视频 "music" | 返回至少一个视频 |
| 9 | Twitter | 搜索 Twitter/X 上的 "AI" | 返回至少一条推文 |
| 10 | Reddit | 搜索 Reddit 上的 "programming" | 返回至少一个帖子 |
| 11 | XiaoHongShu | 在小红书搜索 "美食" | 返回至少一篇笔记 |
| 12 | LinkedIn | 搜索 LinkedIn "software engineer" | 返回至少一个职位 |
| 13 | Xiaoyuzhou | 在小宇宙搜索 "科技" 播客 | 返回至少一个播客 |

## 输出格式

测试完成后输出如下汇总表：

```
╔══════════════════════════════════════════════════════╗
║         Agent Reach 全渠道冒烟测试报告                ║
╚══════════════════════════════════════════════════════╝

零配置渠道：
  ✅ Web                — 成功读取 example.com（320ms）
  ✅ V2EX               — 获取 20 条热门话题（450ms）
  ✅ RSS                — 解析 30 篇文章（280ms）
  ✅ Bilibili           — 搜索返回 10 个视频（600ms）
  ❌ Xueqiu             — API 连接超时（15000ms）
  ❌ Exa Search         — mcporter 未配置

需登录渠道：
  ✅ GitHub             — 搜索返回 8 个仓库（900ms）
  ⏭️ YouTube            — yt-dlp 未安装
  ⏭️ Twitter            — twitter-cli 未安装
  ⏭️ Reddit             — 未登录
  ⏭️ XiaoHongShu        — 未安装任何后端
  ⏭️ LinkedIn           — mcporter 未配置 linkedin
  ⏭️ Xiaoyuzhou         — ffmpeg 未安装

总计：5 通过 / 2 失败 / 6 跳过
```

每个测试的判断标准：
- 返回有效结果 → ✅ 通过
- 报错/超时/返回空 → ❌ 失败（记录原因）
- 工具未安装/未登录/未配置 → ⏭️ 跳过（记录缺少什么）
