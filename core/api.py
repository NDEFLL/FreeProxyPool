"""代理池的只读 HTTP API。

把内存中的 ProxyPool 暴露成 HTTP 接口，方便其他进程或语言按需取代理：

    GET /          路由索引
    GET /get       随机取代理，?https=1 只取支持 HTTPS 的，count 最多 20
    GET /all       按评分列出代理，?limit 最多 500
    GET /stats     池统计（总数、HTTPS 数、平均延迟等）
    GET /health    健康检查，返回服务状态和池大小

只提供读操作，不做鉴权；默认只监听 127.0.0.1，避免被公网滥用。
"""

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .pool import ProxyPool

logger = logging.getLogger(__name__)


def _proxy_view(record: dict) -> dict:
    """把池内的完整记录裁剪成对外字段。"""
    return {
        "proxy": record["proxy"],
        "url": f"http://{record['proxy']}",
        "https": bool(record.get("https")),
        "latency": record.get("latency"),
        "score": record.get("score"),
    }


def create_handler(pool: ProxyPool):
    """为指定代理池创建请求处理器类（每个请求共享同一个 pool）。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "ProxyPool/1.0"

        def do_GET(self) -> None:
            # 只实现 GET：解析路径和查询参数后分发到对应处理函数
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            if parsed.path in ("/", "/index"):
                # 根路径返回接口索引，方便浏览器直接查看
                self._json(
                    {
                        "service": "free-proxy-pool",
                        "routes": ["/get", "/all", "/stats", "/health"],
                        "usage": "/get?https=1&count=3",
                    }
                )
            elif parsed.path == "/get":
                self._handle_get(params)
            elif parsed.path == "/all":
                self._handle_all(params)
            elif parsed.path == "/stats":
                self._json(pool.stats())
            elif parsed.path == "/health":
                self._json({"status": "ok", "pool_size": pool.stats()["total"]})
            else:
                self._json({"error": "not found"}, status=404)

        def _handle_get(self, params: dict) -> None:
            """随机取代理；https=1 时只返回能访问 HTTPS 的代理。"""
            https_only = params.get("https", ["0"])[0].lower() in ("1", "true", "yes")
            try:
                count = int(params.get("count", ["1"])[0])
            except ValueError:
                count = 1
            count = max(1, min(count, 20))  # 限制单次数量，避免一次拉走整个池

            records = pool.pick_many(count, https_only=https_only)
            if not records:
                self._json({"error": "no available proxy"}, status=503)
                return
            self._json({"count": len(records), "proxies": [_proxy_view(r) for r in records]})

        def _handle_all(self, params: dict) -> None:
            """按评分从高到低列出代理，便于人工排查。"""
            https_only = params.get("https", ["0"])[0].lower() in ("1", "true", "yes")
            try:
                limit = int(params.get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            limit = max(1, min(limit, 500))
            proxies = pool.list_proxies(limit=limit, https_only=https_only)
            self._json({"count": len(proxies), "proxies": [_proxy_view(r) for r in proxies]})

        def _json(self, data: dict, status: int = 200) -> None:
            """统一输出 UTF-8 JSON，并带上正确的 Content-Length。"""
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            # 默认实现会直接打印到 stderr，这里降级到 DEBUG，避免定时刷新时刷屏
            logger.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def run_server(pool: ProxyPool, host: str = "127.0.0.1", port: int = 5010) -> None:
    """启动多线程 HTTP 服务并阻塞运行，直到 Ctrl+C 或进程结束。

    ThreadingHTTPServer 让每个请求独立成线程，慢客户端不会阻塞其他请求；
    daemon_threads=True 保证主线程退出时不会卡在未结束的连接上。
    """
    server = ThreadingHTTPServer((host, port), create_handler(pool))
    server.daemon_threads = True
    logger.info("代理池 API 已启动: http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
