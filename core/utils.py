"""代理字符串规范化工具。"""

import re
from typing import Iterable, Optional

_IPV4 = r"(?:\d{1,3}\.){3}\d{1,3}"
_STRICT_RE = re.compile(rf"^(?:https?://)?({_IPV4}):(\d{{1,5}})$")
_LOOSE_RE = re.compile(rf"(?:https?://)?({_IPV4}):(\d{{1,5}})")


def normalize_proxy(value: object) -> Optional[str]:
    """把 'ip:port' / 'http://ip:port' 规范化为 'ip:port'，非法返回 None。"""
    if not value:
        return None
    match = _STRICT_RE.match(str(value).strip())
    if not match:
        return None
    ip, port_text = match.group(1), match.group(2)
    port = int(port_text)
    if port < 1 or port > 65535:
        return None
    if any(int(part) > 255 for part in ip.split(".")):
        return None
    return f"{ip}:{port}"


def extract_proxies(text: str) -> set[str]:
    """从纯文本中提取所有合法 ip:port。"""
    proxies: set[str] = set()
    for ip, port in _LOOSE_RE.findall(text or ""):
        proxy = normalize_proxy(f"{ip}:{port}")
        if proxy:
            proxies.add(proxy)
    return proxies


def to_proxy_set(pairs: Iterable[tuple[str, str]]) -> set[str]:
    proxies: set[str] = set()
    for ip, port in pairs:
        proxy = normalize_proxy(f"{ip}:{port}")
        if proxy:
            proxies.add(proxy)
    return proxies
