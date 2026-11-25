"""代理可用性并发校验。"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from . import config

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


def check_proxy(proxy: str, timeout: float | None = None) -> dict:
    """校验单个代理。

    先测 HTTPS，失败再测 HTTP。返回：
    {"proxy", "ok", "https", "http", "latency"}
    """
    timeout = timeout or config.VALIDATE_TIMEOUT
    proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
    result = {"proxy": proxy, "ok": False, "https": False, "http": False, "latency": None}

    started = time.perf_counter()
    try:
        resp = _session().get(config.HTTPS_CHECK_URL, proxies=proxies, timeout=timeout)
        if resp.status_code == 200 and config.CHECK_MARKER in resp.content:
            result.update(
                ok=True,
                https=True,
                http=True,
                latency=int((time.perf_counter() - started) * 1000),
            )
            return result
    except requests.RequestException:
        pass

    started = time.perf_counter()
    try:
        resp = _session().get(config.HTTP_CHECK_URL, proxies=proxies, timeout=timeout)
        if resp.status_code == 200 and config.CHECK_MARKER in resp.content:
            result.update(
                ok=True,
                http=True,
                latency=int((time.perf_counter() - started) * 1000),
            )
    except requests.RequestException:
        pass
    return result


def check_many(proxies, workers: int | None = None, timeout: float | None = None):
    """并发校验，按完成顺序产出结果。"""
    proxies = list(dict.fromkeys(proxies))
    if not proxies:
        return
    workers = min(workers or config.CHECK_WORKERS, len(proxies))
    timeout = timeout or config.VALIDATE_TIMEOUT
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(check_proxy, proxy, timeout): proxy for proxy in proxies}
        for future in as_completed(future_map):
            proxy = future_map[future]
            try:
                yield future.result()
            except Exception as exc:  # 兜底，避免单个任务异常中断整轮
                logger.debug("校验 %s 异常: %s", proxy, exc)
                yield {"proxy": proxy, "ok": False, "https": False, "http": False, "latency": None}
