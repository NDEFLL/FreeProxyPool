"""免费代理源采集。

每个源返回 set[str]（ip:port）。单个源失败只记录日志，不影响其它源。
"""

import json
import logging
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from . import config
from .utils import extract_proxies, normalize_proxy, to_proxy_set

logger = logging.getLogger(__name__)

_local = threading.local()


def _session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        session.headers.update(config.HEADERS)
        _local.session = session
    return session


def _fetch(url: str, encoding: str | None = None) -> str:
    resp = _session().get(url, timeout=config.FETCH_TIMEOUT)
    resp.raise_for_status()
    if encoding:
        resp.encoding = encoding
    return resp.text


def _table_pairs(html: str) -> set[str]:
    """解析 <td>ip</td><td>port</td> 结构的表格（89ip / ip3366）。"""
    pairs = re.findall(
        r"<td[^>]*>\s*((?:\d{1,3}\.){3}\d{1,3})\s*</td>\s*<td[^>]*>\s*(\d{1,5})\s*</td>",
        html,
    )
    return to_proxy_set(pairs)


def fetch_89ip() -> set[str]:
    proxies: set[str] = set()
    for page in range(1, 4):
        html = _fetch(f"https://www.89ip.cn/index_{page}.html")
        proxies |= _table_pairs(html)
    return proxies

def fetch_ip3366() -> set[str]:
    proxies: set[str] = set()
    for stype in (1, 2):
        for page in (1, 2):
            html = _fetch(
                f"http://www.ip3366.net/free/?stype={stype}&page={page}",
                encoding="gbk",
            )
            proxies |= _table_pairs(html)
    return proxies

def fetch_zdaye() -> set[str]:
    proxies: set[str] = set()
    for page in (1, 2):
        url = "https://www.zdaye.com/free/" if page == 1 else f"https://www.zdaye.com/free/{page}/"
        html = _fetch(url)
        pairs = re.findall(
            r'class="proxy_ip">\s*((?:\d{1,3}\.){3}\d{1,3})\s*</p>\s*'
            r'<p class="proxy_port">\s*Port[：:]\s*(\d{1,5})',
            html,
        )
        proxies |= to_proxy_set(pairs)
    return proxies

def fetch_free_proxy_list() -> set[str]:
    html = _fetch("https://free-proxy-list.net/")
    pairs = re.findall(
        r"<td[^>]*>\s*((?:\d{1,3}\.){3}\d{1,3}):(\d{1,5})\s*</td>",
        html,
    )
    proxies = to_proxy_set(pairs)
    # 页面主要代理列表放在 textarea 中
    match = re.search(r"<textarea[^>]*>(.*?)</textarea>", html, re.S | re.I)
    if match:
        proxies |= extract_proxies(match.group(1))
    return proxies

def fetch_geonode() -> set[str]:
    text = _fetch(
        "https://proxylist.geonode.com/api/proxy-list"
        "?limit=500&page=1&sort_by=lastChecked&sort_type=desc"
    )
    data = json.loads(text)
    proxies: set[str] = set()
    for item in data.get("data", []):
        protocols = item.get("protocols") or []
        if not any(proto in ("http", "https") for proto in protocols):
            continue
        proxy = normalize_proxy(f"{item.get('ip')}:{item.get('port')}")
        if proxy:
            proxies.add(proxy)
    return proxies

def fetch_proxyscrape() -> set[str]:
    text = _fetch(
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http"
        "&timeout=5000&country=all&ssl=all&anonymity=all"
    )
    return extract_proxies(text)

def fetch_openproxylist() -> set[str]:
    return extract_proxies(_fetch("https://api.openproxylist.xyz/http.txt"))

def fetch_proxyspace() -> set[str]:
    return extract_proxies(_fetch("https://proxyspace.pro/http.txt"))

def fetch_speedx() -> set[str]:
    return extract_proxies(
        _fetch("https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt")
    )

def fetch_monosans() -> set[str]:
    return extract_proxies(
        _fetch("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt")
    )

def fetch_scrape_center() -> set[str]:
    return extract_proxies(_fetch("https://proxypool.scrape.center/all"))


# 按优先级排列：国内源优先，保证限量时先校验更可能可用的代理
SOURCES = [
    ("89ip", fetch_89ip),
    ("ip3366", fetch_ip3366),
    ("zdaye", fetch_zdaye),
    ("free-proxy-list", fetch_free_proxy_list),
    ("geonode", fetch_geonode),
    ("proxyscrape", fetch_proxyscrape),
    ("openproxylist", fetch_openproxylist),
    ("proxyspace", fetch_proxyspace),
    ("speedx", fetch_speedx),
    ("monosans", fetch_monosans),
    ("scrape-center", fetch_scrape_center),
]


def fetch_all() -> list[str]:
    """并发抓取全部源，返回按源优先级去重后的代理列表。"""
    found_by_name: dict[str, set[str]] = {}
    with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as executor:
        future_map = {executor.submit(func): name for name, func in SOURCES}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                proxies = future.result()
            except Exception as exc:  # 单源异常不影响整体
                logger.warning("源 %s 抓取失败: %s", name, exc)
                continue
            if len(proxies) > config.MAX_PER_SOURCE:
                proxies = set(random.sample(sorted(proxies), config.MAX_PER_SOURCE))
            found_by_name[name] = proxies
            logger.info("源 %s 获取 %d 个代理", name, len(proxies))

    ordered: list[str] = []
    seen: set[str] = set()
    for name, _ in SOURCES:
        for proxy in found_by_name.get(name, ()):
            if proxy not in seen:
                seen.add(proxy)
                ordered.append(proxy)
    logger.info("共获取去重后 %d 个候选代理", len(ordered))
    return ordered
