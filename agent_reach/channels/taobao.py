# -*- coding: utf-8 -*-
"""淘宝 — 商品搜索与详情 via ecommerce-cli (需 Cookie)."""
from .base import Channel
from ..probe import probe_command


class TaobaoChannel(Channel):
    name = "taobao"
    description = "淘宝商品搜索与详情（ecommerce-cli，需登录 Cookie）"
    backends = ["ecommerce-cli"]
    tier = 1  # 需要登录 Cookie

    def can_handle(self, url: str) -> bool:
        return "taobao.com" in url.lower()

    def check(self, config=None):
        self.active_backend = None
        result = probe_command("ecommerce-cli", ["check", "taobao"], timeout=30, package="ecommerce-cli")
        if result.status == "missing":
            return "off", "ecommerce-cli 未安装。安装：pipx install ecommerce-cli && python -m playwright install chromium"
        if result.status == "broken":
            return "error", f"ecommerce-cli 已损坏：{result.hint}"
        try:
            from ._ecom_utils import parse_ecom_check_output
            data = parse_ecom_check_output(result.output)
            status = data.get("status", "error")
            if status == "ok":
                self.active_backend = self.backends[0]
                return "ok", data.get("message", "淘宝可用")
            if status == "no-cookie":
                return "warn", data.get("message", "淘宝需要登录 Cookie。运行：agent-reach configure --from-browser chrome")
            return "warn", data.get("message", "")
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
