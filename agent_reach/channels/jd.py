# -*- coding: utf-8 -*-
"""京东 — 商品搜索、详情、评论和价格历史 via ecommerce-cli."""
from .base import Channel
from ..probe import probe_command


class JdChannel(Channel):
    name = "jd"
    description = "京东商品搜索、详情、评论与价格历史（ecommerce-cli）"
    backends = ["ecommerce-cli"]
    tier = 1  # 详情和评论需要登录 Cookie

    def can_handle(self, url: str) -> bool:
        return "jd.com" in url.lower()

    def check(self, config=None):
        self.active_backend = None
        result = probe_command("ecommerce-cli", ["check", "jd"], timeout=25, package="ecommerce-cli")
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
                return "ok", data.get("message", "京东可用")
            return "warn", data.get("message", "") + "\n提示：登录后可获取详情与评论：agent-reach configure --from-browser chrome"
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
