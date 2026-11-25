"""启动代理池服务：加载已有代理、后台定时刷新、提供 HTTP API。"""

import logging
import threading
import time

from core import config
from core.api import run_server
from core.pool import ProxyPool


def refresh_loop(pool: ProxyPool) -> None:
    """后台刷新：启动后立即跑一轮，之后每隔 REFRESH_INTERVAL 秒一次。"""
    while True:
        try:
            pool.refresh()
        except Exception:
            logging.exception("定时刷新失败")
        time.sleep(config.REFRESH_INTERVAL)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    pool = ProxyPool()

    threading.Thread(
        target=refresh_loop,
        args=(pool,),
        name="proxy-pool-refresh",
        daemon=True,
    ).start()

    try:
        run_server(pool, host="127.0.0.1", port=5010)
    except OSError as exc:
        logging.error("服务启动失败: %s（端口 5010 可能已被占用）", exc)


if __name__ == "__main__":
    main()
