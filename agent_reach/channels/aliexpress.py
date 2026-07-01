# -*- coding: utf-8 -*-
"""AliExpress — product search and details via ecommerce-cli (Playwright)."""
from .base import Channel
from ..probe import probe_command


class AliexpressChannel(Channel):
    name = "aliexpress"
    description = "AliExpress 商品搜索与详情（ecommerce-cli）"
    backends = ["ecommerce-cli"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        return "aliexpress.com" in url.lower()

    def check(self, config=None):
        self.active_backend = None
        result = probe_command("ecommerce-cli", ["aliexpress", "check"], timeout=25, package="ecommerce-cli")
        if result.status == "missing":
            return "off", "ecommerce-cli 未安装。安装：pipx install ecommerce-cli && python -m playwright install chromium"
        if result.status == "broken":
            return "error", f"ecommerce-cli 已损坏：{result.hint}"
        try:
            import json
            data = json.loads(result.output.strip().split("\n")[-1])
            if data.get("status") == "ok":
                self.active_backend = self.backends[0]
                return "ok", data.get("message", "AliExpress 可用")
            return "warn", data.get("message", "")
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
