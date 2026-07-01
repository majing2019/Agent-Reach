# -*- coding: utf-8 -*-
"""Amazon — product search, details, reviews, and price history via ecommerce-cli."""
from .base import Channel
from ..probe import probe_command


class AmazonChannel(Channel):
    name = "amazon"
    description = "Amazon 商品搜索、详情、评论与价格历史（ecommerce-cli）"
    backends = ["ecommerce-cli"]
    tier = 1  # May need cookie or proxy to avoid captcha

    def can_handle(self, url: str) -> bool:
        return "amazon.com" in url.lower() or "amazon.cn" in url.lower()

    def check(self, config=None):
        self.active_backend = None
        result = probe_command("ecommerce-cli", ["amazon", "check"], timeout=25, package="ecommerce-cli")
        if result.status == "missing":
            return "off", "ecommerce-cli 未安装。安装：pipx install ecommerce-cli && python -m playwright install chromium"
        if result.status == "broken":
            return "error", f"ecommerce-cli 已损坏：{result.hint}"
        try:
            import json
            data = json.loads(result.output.strip().split("\n")[-1])
            status = data.get("status", "error")
            if status == "ok":
                self.active_backend = self.backends[0]
                return "ok", data.get("message", "Amazon 可用（含价格历史）")
            return "warn", data.get("message", "") + "\n提示：Amazon 反爬较强，建议配置 Cookie 或使用代理"
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
