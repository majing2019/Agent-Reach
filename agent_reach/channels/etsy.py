# -*- coding: utf-8 -*-
"""Etsy — product search and details via ecommerce-cli (Playwright)."""

from .base import Channel
from ..probe import probe_command


class EtsyChannel(Channel):
    name = "etsy"
    description = "Etsy 手工艺品搜索与详情（ecommerce-cli）"
    backends = ["ecommerce-cli"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        return "etsy.com" in url.lower()

    def check(self, config=None):
        self.active_backend = None
        result = probe_command("ecommerce-cli", ["etsy", "check"], timeout=20, package="ecommerce-cli")

        if result.status == "missing":
            return "off", "ecommerce-cli 未安装。安装：pipx install ecommerce-cli && python -m playwright install chromium"
        if result.status == "broken":
            return "error", f"ecommerce-cli 已损坏：{result.hint}"

        try:
            import json
            lines = result.output.strip().split("\n")
            data = json.loads(lines[-1])
            status = data.get("status", "error")
            message = data.get("message", "")
            if status == "ok":
                self.active_backend = self.backends[0]
                return "ok", message
            return "warn", message
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
