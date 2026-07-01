# -*- coding: utf-8 -*-
"""拼多多 — 商品搜索与详情 via ecommerce-cli (需登录，反爬极强)."""
from .base import Channel
from ..probe import probe_command


class PinduoduoChannel(Channel):
    name = "pinduoduo"
    description = "拼多多商品搜索与详情（ecommerce-cli，反爬极强）"
    backends = ["ecommerce-cli"]
    tier = 2  # 需要复杂配置

    def can_handle(self, url: str) -> bool:
        return any(d in url.lower() for d in ["pinduoduo.com", "yangkeduo.com"])

    def check(self, config=None):
        self.active_backend = None
        result = probe_command("ecommerce-cli", ["pinduoduo", "check"], timeout=25, package="ecommerce-cli")
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
                return "ok", data.get("message", "拼多多可用")
            return "warn", data.get("message", "")
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
