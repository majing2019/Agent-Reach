# -*- coding: utf-8 -*-
"""天猫 — 品牌商品搜索与详情 via ecommerce-cli (需 Cookie)."""
from .base import Channel
from ..probe import probe_command


class TmallChannel(Channel):
    name = "tmall"
    description = "天猫品牌商品搜索与详情（ecommerce-cli，需登录 Cookie）"
    backends = ["ecommerce-cli"]
    tier = 1  # 需要登录 Cookie

    def can_handle(self, url: str) -> bool:
        return "tmall.com" in url.lower()

    def check(self, config=None):
        self.active_backend = None
        result = probe_command("ecommerce-cli", ["tmall", "check"], timeout=25, package="ecommerce-cli")
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
                return "ok", data.get("message", "天猫可用")
            if status == "no-cookie":
                return "warn", data.get("message", "天猫需要登录 Cookie。运行：agent-reach configure --from-browser chrome")
            return "warn", data.get("message", "")
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
