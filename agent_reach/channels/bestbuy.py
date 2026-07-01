# -*- coding: utf-8 -*-
"""Best Buy — product search and details via ecommerce-cli (Playwright)."""

from .base import Channel
from ..probe import probe_command


class BestBuyChannel(Channel):
    name = "bestbuy"
    description = "Best Buy 商品搜索、详情与评论（ecommerce-cli）"
    backends = ["ecommerce-cli"]
    tier = 0  # No login required for search (though proxy may be needed from CN)

    def can_handle(self, url: str) -> bool:
        return "bestbuy.com" in url.lower()

    def check(self, config=None):
        self.active_backend = None

        # Probe ecommerce-cli installation
        result = probe_command(
            "ecommerce-cli", ["check", "bestbuy"], timeout=30, package="ecommerce-cli"
        )

        if result.status == "missing":
            return "off", (
                "ecommerce-cli 未安装。安装步骤：\n"
                "  1. pipx install ecommerce-cli\n"
                "  2. python -m playwright install chromium\n"
                "  3. （国内访问美国站点可能需要代理）ecommerce-cli bestbuy check --proxy <代理地址>"
            )
        if result.status == "broken":
            return "error", f"ecommerce-cli 已损坏：{result.hint}"

        # Parse the JSON check output
        # probe_command captures stdout+stderr; find the JSON line
        try:
            from ._ecom_utils import parse_ecom_check_output

            data = parse_ecom_check_output(result.output)  # Last line should be the JSON

            status = data.get("status", "error")
            message = data.get("message", "")

            if status == "ok":
                self.active_backend = self.backends[0]
                return "ok", message
            elif status == "no-cookie":
                return "warn", message
            elif status == "warn":
                return "warn", message
            else:
                return "warn", (
                    f"{message}\n"
                    "提示：中国大陆访问美国站点可能需要代理。使用：\n"
                    "  agent-reach config set bestbuy_proxy http://host:port"
                )
        except Exception:
            return "warn", f"ecommerce-cli check 输出异常：{result.output[:200]}"
