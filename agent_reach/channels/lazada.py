# -*- coding: utf-8 -*-
"""Lazada — product search and details via ecommerce-cli (Playwright)."""
from .base import Channel
from ..probe import probe_command


class LazadaChannel(Channel):
    name = "lazada"
    description = "Lazada 商品搜索与详情（ecommerce-cli）"
    backends = ["ecommerce-cli"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        return any(d in url.lower() for d in ["lazada.com", "lazada.sg", "lazada.co"])

    def check(self, config=None):
        self.active_backend = None
        result = probe_command("ecommerce-cli", ["check", "lazada"], timeout=30, package="ecommerce-cli")
        if result.status == "missing":
            return "off", "ecommerce-cli 未安装。安装：pipx install ecommerce-cli && python -m playwright install chromium"
        if result.status == "broken":
            return "error", f"ecommerce-cli 已损坏：{result.hint}"
        try:
            from ._ecom_utils import parse_ecom_check_output
            data = parse_ecom_check_output(result.output)
            if data.get("status") == "ok":
                self.active_backend = self.backends[0]
                return "ok", data.get("message", "Lazada 可用")
            return "warn", data.get("message", "")
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
