# -*- coding: utf-8 -*-
"""Target — product search and details via ecommerce-cli (Playwright)."""

from .base import Channel
from ..probe import probe_command


class TargetChannel(Channel):
    name = "target"
    description = "Target 商品搜索、详情与评论（ecommerce-cli）"
    backends = ["ecommerce-cli"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        return "target.com" in url.lower()

    def check(self, config=None):
        self.active_backend = None
        result = probe_command("ecommerce-cli", ["check", "target"], timeout=30, package="ecommerce-cli")

        if result.status == "missing":
            return "off", "ecommerce-cli 未安装。安装：pipx install ecommerce-cli && python -m playwright install chromium"
        if result.status == "broken":
            return "error", f"ecommerce-cli 已损坏：{result.hint}"

        try:
            from ._ecom_utils import parse_ecom_check_output
            data = parse_ecom_check_output(result.output)
            status = data.get("status", "error")
            message = data.get("message", "")
            if status == "ok":
                self.active_backend = self.backends[0]
                return "ok", message
            return "warn", message
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
