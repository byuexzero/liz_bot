"""把 ``#上传`` 的实际发包交给「本机助手」—— 出口 IP 问题的出路。

为什么存在
==========
云服务器（腾讯云轻量 ``43.142.50.149``）的出口 IP 被舞萌服务端拒：
``GetGameSettingApi`` / ``GetUserPreviewApi`` **三次全 HTTP 200 + 0 字节**
（连纯读接口都空 ⇒ 入口层拦截）。同日**本机**同代码同账号**完整成功**
（``playCount 11 → 12``）⇒ 代码 / 凭据 / 挂载全无罪，**差异只在出口 IP**。

要拿一个干净出口，其实**不需要服务器提供任何东西** —— 机器人是纯出站的
WebSocket 客户端（见 ``deploy/docker-compose.yml`` 的注释）。所以让**本机**
去跑那几次请求即可。

怎么跑（**没有公网入站、没有内网穿透、没有第三方依赖**）
----------------------------------------------------
1. 服务器这边把任务**落盘**到 :data:`liz_bot.runtime_paths.UPLOAD_QUEUE_DIR`
   的 ``pending/``，然后轮询 ``done/``。
2. 本机助手（``_tools/upload_worker.py``）**主动** SSH 过来，把 ``pending/``
   里的任务 ``mv`` 到 ``claimed/``，在**本机**执行上传，再把结果写进 ``done/``。
3. 服务器读到结果 ⇒ 渲染成回复。

全程**由本机发起连接**，所以不需要在服务器上开任何端口。

⚠️ 二维码的暴露面
-----------------
任务文件里**有完整二维码**（等同扫卡那张卡）。三重收敛：

* 文件按 ``0600`` 写入，且落在 ``deploy/data/``（已被 ``.gitignore`` 排除）；
* 本机助手**取走即删**（``mv`` 走），服务器读到结果后也删；
* 日志里只出现 ``mask_qr`` 的脱敏写法。

⚠️ 心跳用**文件 mtime**，不用文件里的时间戳
------------------------------------------
两边机器时钟未必一致。心跳文件由本机助手经 SSH 在**服务器上**创建 ⇒
它的 mtime 是**服务器自己的时钟** ⇒ 服务器读 mtime 判活**没有时钟偏差问题**。
（把时间戳写进文件内容再比对就会踩这个坑。）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from liz_bot import runtime_paths

logger = logging.getLogger(__name__)

#: 开这个环境变量才启用远端上传。**默认关** —— 本机助手没跑起来时，
#: 开了就是每次 ``#上传`` 都白等一轮超时。
ENV_ENABLE = "MM_REMOTE_UPLOAD"

#: 等本机助手回结果的上限（秒）。真跑一次约 80 秒（60s 模拟游玩 + 16 次
#: 请求的节流），再留出本机助手轮询的间隔与几次 SSH 往返。
#: ⚠️ 别超过 QQ 被动回复的 5 分钟有效期（``msg_seq`` 那条通路）。
ENV_TIMEOUT = "MM_REMOTE_TIMEOUT"
DEFAULT_TIMEOUT = 150.0

#: 心跳多久算「助手不在线」（秒）。助手默认 5 秒轮询一次，留 6 倍余量。
ENV_STALE = "MM_REMOTE_STALE"
DEFAULT_STALE = 30.0

#: 轮询结果的间隔（秒）。
ENV_POLL = "MM_REMOTE_POLL"
DEFAULT_POLL = 1.0

#: 结果文件里回传的字段（服务器据此重建 ``UploadReport``）。
_RESULT_FIELDS = ("ok", "stage", "message", "before", "after")


def enabled() -> bool:
    """是否走「本机助手」通道。"""
    return bool(os.environ.get(ENV_ENABLE))


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def queue_dir() -> Path:
    return Path(runtime_paths.UPLOAD_QUEUE_DIR)


def _sub(name: str) -> Path:
    return queue_dir() / name


def heartbeat_path() -> Path:
    return queue_dir() / "heartbeat"


def agent_alive() -> tuple[bool, float]:
    """本机助手是否在线 → ``(在线?, 心跳已过去多少秒)``。

    判据是心跳文件的 **mtime**（由本机助手在服务器侧创建 ⇒ 服务器时钟），
    不是文件内容里的时间戳 —— 见模块 docstring。
    """
    path = heartbeat_path()
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False, float("inf")
    return age <= _float_env(ENV_STALE, DEFAULT_STALE), age


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """原子 + 0600 写入。

    ⚠️ 先写临时文件再 ``os.replace`` —— 助手可能正好在 ``cat`` 它，
    直接覆盖会让对方读到半截 JSON。
    ⚠️ 权限用 ``0o600``：任务里有完整二维码。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    # ⚠️ 上面那个 ``0o600`` **不够**：``O_CREAT`` 对**已存在**的临时文件不会重置
    #    权限（上一次崩溃留下的 ``.tmp`` 若是 0o666，这次会沿用）。所以显式再
    #    ``chmod`` 一次。``os.open`` 的 mode 还会被 umask 削，而 umask 只能**去掉**
    #    位，所以这两步合起来在 POSIX 上一定 ≤ 0o600。
    #    （Windows 上权限位不按 POSIX 语义生效，此处是空操作 —— 但服务器是 Linux。）
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # 某些文件系统不支持 chmod —— 不该因此让上传失败
        logger.warning("远端上传：chmod 0600 失败（%s）", tmp, exc_info=True)
    os.replace(tmp, path)


def new_task_id() -> str:
    """``20260927T192600-a1b2c3`` —— 按时间可排序，且带随机后缀防撞。"""
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"


def submit(
    score: dict[str, Any],
    qr: str,
    *,
    note: str | None = None,
    dry: bool = False,
) -> str:
    """把一次上传任务落盘，返回 ``task_id``。

    成绩已经在服务器侧算好了（``fill_note_counts`` 跑过），本机助手只是
    「照着发包」—— 这样「要传什么」只有一处实现，不会两边算出不同的结果。
    """
    task_id = new_task_id()
    _sub("pending").mkdir(parents=True, exist_ok=True)
    _write_json(_sub("pending") / f"{task_id}.json", {
        "id": task_id,
        "created_at": time.time(),
        "qr": qr,
        "score": score,
        "note": note,
        "dry": bool(dry),
    })
    logger.info("远端上传：任务已落盘 id=%s（等本机助手）", task_id)
    return task_id


async def wait(task_id: str, timeout: float | None = None) -> dict[str, Any] | None:
    """轮询结果文件；超时返回 ``None``。"""
    limit = _float_env(ENV_TIMEOUT, DEFAULT_TIMEOUT) if timeout is None else timeout
    interval = _float_env(ENV_POLL, DEFAULT_POLL)
    path = _sub("done") / f"{task_id}.json"
    deadline = time.monotonic() + limit

    while True:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # 半截文件：本机助手也是原子写的，走到这里说明真出问题了。
                # 不当成失败 —— 下一轮再读一次，给写入留出完成时间。
                logger.warning("远端上传：结果文件读不动 id=%s，稍后重试", task_id)
            else:
                logger.info(
                    "远端上传：收到结果 id=%s ok=%s", task_id, payload.get("ok")
                )
                return payload
        if time.monotonic() >= deadline:
            logger.warning("远端上传：等本机助手超时 id=%s（%.0fs）", task_id, limit)
            return None
        await asyncio.sleep(interval)


def cleanup(task_id: str) -> None:
    """删掉这次任务的残留（三个目录都扫一遍）。

    ⚠️ 必须删 ``pending`` —— 助手没起来时二维码会一直躺在盘上。
    """
    for sub in ("pending", "claimed", "done"):
        try:
            (_sub(sub) / f"{task_id}.json").unlink()
        except OSError:
            pass


def report_fields(result: dict[str, Any]) -> dict[str, Any]:
    """从结果 JSON 里挑出重建 ``UploadReport`` 需要的字段（缺的补 ``None``）。"""
    return {key: result.get(key) for key in _RESULT_FIELDS}


def describe() -> str:
    """一行状态，供启动日志。"""
    if not enabled():
        return "远端上传：关（#上传 在服务器本机执行）"
    alive, age = agent_alive()
    where = queue_dir()
    if alive:
        return f"远端上传：开 · 本机助手在线（{age:.0f}s 前心跳）· 队列 {where}"
    shown = "无心跳" if age == float("inf") else f"{age:.0f}s 未心跳"
    return f"远端上传：开 · ⚠ 本机助手不在线（{shown}）· 队列 {where}"
