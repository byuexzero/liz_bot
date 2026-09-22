"""极简健康检查 HTTP 监听 —— 让容器平台认为本服务「活着」。

为什么需要
----------
本机器人是 botpy 的 **WebSocket 客户端模式**：只主动连出去，
本地**不监听任何端口**。这在裸机 / 自建 VPS 上完全没问题，
但在**容器平台**上会踩坑 —— 多数平台以「容器是否监听并响应端口」作为存活
判据，一个从不监听端口的容器很容易被判为不健康而反复重启（表现为机器人
每隔几分钟掉线重连，日志里看不出原因）。

所以这里起一个极小的 HTTP 服务，只做两件事：绑定端口、返回 200。

- 内存开销约 1 MB，相对机器人本身约 42 MB 的占用可忽略
- 只返回状态与运行时长，**不暴露任何凭据**
- **未设置端口时完全 no-op**，本地开发与裸机部署不受影响

启用方式
--------
设置 ``HEALTHZ_PORT``（优先）或 ``PORT``（多数平台会自动注入）为端口号::

    HEALTHZ_PORT=8080

监听地址默认 ``0.0.0.0``（容器内必须如此，平台的代理才能连进来），
可用 ``HEALTHZ_HOST`` 覆盖。端口为 0 或无法解析时视为禁用。
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_STARTED_AT = time.time()

#: 对外暴露的运行状态。始终保持 HTTP 200 —— 启动阶段返回非 200 会被平台
#: 判定为启动失败并杀掉容器，反而制造问题；状态细节放在响应体里。
_state = {"bot": "starting"}


def set_status(status: str) -> None:
    """更新对外暴露的运行状态（例如 ``on_ready`` 之后设为 ``"ready"``）。"""
    _state["bot"] = status


def _resolve_port() -> int:
    """解析监听端口；未配置或非法时返回 0（表示禁用）。"""
    for name in ("HEALTHZ_PORT", "PORT"):
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            continue
        try:
            port = int(raw)
        except ValueError:
            continue
        if 0 < port < 65536:
            return port
    return 0


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 + 准确的 Content-Length，平台侧的健康检查客户端可复用连接
    protocol_version = "HTTP/1.1"

    def _respond(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定命名
        payload = {
            "status": "ok",
            "bot": _state["bot"],
            "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        }
        self._respond(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_HEAD(self) -> None:  # noqa: N802
        self._respond(b"")

    def log_message(self, *args) -> None:
        """静默。

        健康检查会被平台高频轮询，走默认实现会往 stderr 刷大量访问日志，
        既污染 botpy 的日志文件，也让真正有用的日志被淹没。
        """


def start() -> int:
    """在后台线程启动健康检查服务。

    :return: 实际监听的端口；未启用时返回 ``0``。
    """
    port = _resolve_port()
    if not port:
        return 0

    host = (os.environ.get("HEALTHZ_HOST") or "").strip() or "0.0.0.0"

    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True

    thread = threading.Thread(
        target=server.serve_forever, name="healthz", daemon=True
    )
    thread.start()
    return port
