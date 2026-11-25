"""免费代理 IP 池：采集、校验、评分、定时刷新与 HTTP API。"""

from .pool import ProxyPool
from .validator import check_proxy

__all__ = ["ProxyPool", "check_proxy"]
