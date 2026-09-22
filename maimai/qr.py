"""A.I.M.E. 扫码接口。

把玩家扫卡得到的二维码兑换成 ``userId`` 与 ``token``。

二维码结构
----------
::

    SGWCMAID 260101120000 <64 字符载荷>
    ^^^^^^^^ ^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^^
    8 字符    12 字符      64 字符
    前缀      时间戳       载荷

上面是**结构示意**，不是真实二维码。真实二维码等同账号密码，
只存在于 ``.env``，不入库。

与旧实现（``sdgb/sdgb.py`` 的 ``qr_api``）的差异
--------------------------------------------------
旧实现**丢弃**二维码自带的时间戳，改用当前东京时间重新生成。
本实现优先**提取二维码内的时间戳**（Lionheart 的做法），
因为签名必须与该二维码对应；只有解析不出时间戳时才退回本地时间。

:func:`qr_api` 保持同步，签名与旧实现一致，可直接替换。
异步场景请用 :func:`qr_api_async`。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

import httpx
import pytz

from . import config

__all__ = ["qr_api", "qr_api_async", "parse_qr", "QrApiError"]

logger = logging.getLogger(__name__)

#: 前缀长度（"SGWCMAID"）
QR_PREFIX_LEN = 8
#: 时间戳长度（"YYMMDDHHMMSS"）
QR_TIMESTAMP_LEN = 12
#: 载荷长度
QR_PAYLOAD_LEN = 64

#: errorID 语义
ERROR_ID_SUCCESS = 0
ERROR_ID_EXPIRED = 1
ERROR_ID_BAD_PARAMS = 50


class QrApiError(RuntimeError):
    """扫码接口调用失败。"""


def _local_timestamp() -> str:
    """当前东京时间，``YYMMDDHHMMSS`` 格式。"""
    return datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%y%m%d%H%M%S")


def parse_qr(qr_code: str) -> tuple[str, str]:
    """把二维码拆成 ``(时间戳, 载荷)``。

    标准二维码结构为 ``前缀(8) + 时间戳(12) + 载荷(64)``。
    若不符合该结构，时间戳退回当前时间、载荷退回末 64 字符
    （与旧实现行为一致）。

    :returns: ``(timestamp, payload)``，均为字符串
    """
    code = (qr_code or "").strip()
    if not code:
        raise ValueError("二维码为空")

    start = QR_PREFIX_LEN
    end = QR_PREFIX_LEN + QR_TIMESTAMP_LEN
    if len(code) >= end + 1:
        candidate = code[start:end]
        if candidate.isdigit():
            return candidate, code[end:]

    logger.debug("二维码结构不符合预期（长度 %d），退回本地时间戳", len(code))
    payload = code[-QR_PAYLOAD_LEN:] if len(code) > QR_PAYLOAD_LEN else code
    return _local_timestamp(), payload


def _build_request(qr_code: str) -> tuple[str, dict[str, Any]]:
    """构造签名与请求体。"""
    timestamp, payload = parse_qr(qr_code)
    auth_key = hashlib.sha256(
        (config.CHIP_ID + timestamp + config.AIME_SALT).encode("utf-8")
    ).hexdigest().upper()

    body = {
        "chipID": config.CHIP_ID,
        "openGameID": "MAID",
        "key": auth_key,
        "qrCode": payload,
        "timestamp": timestamp,
    }
    return timestamp, body


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Contention": "Keep-Alive",
        "User-Agent": "WC_AIME_LIB",
    }


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200:
        raise QrApiError(f"扫码接口返回 HTTP {response.status_code}")
    try:
        return json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise QrApiError(f"扫码接口响应不是合法 JSON：{exc}") from exc


def qr_api(qr_code: str) -> dict[str, Any]:
    """同步扫码。签名与旧实现一致，可直接替换。

    :returns: 接口原始响应字典，含 ``errorID`` / ``userID`` / ``token`` 等

    ::

        ERROR_ID_SUCCESS    = 0   成功
        ERROR_ID_EXPIRED    = 1   二维码已过期
        ERROR_ID_BAD_PARAMS = 50  参数错误
    """
    timestamp, body = _build_request(qr_code)
    logger.debug("扫码请求 timestamp=%s", timestamp)

    kwargs: dict[str, Any] = {
        "data": json.dumps(body, separators=(",", ":")),
        "headers": _headers(),
        "timeout": config.REQUEST_TIMEOUT,
    }
    if config.PROXY_URL:
        kwargs["proxy"] = config.PROXY_URL
    if config.INSECURE_TLS:
        kwargs["verify"] = False

    try:
        response = httpx.post(config.AIME_QR_URL, **kwargs)
    except httpx.HTTPError as exc:
        raise QrApiError(f"扫码接口网络错误：{exc}") from exc
    return _parse_response(response)


async def qr_api_async(qr_code: str) -> dict[str, Any]:
    """异步扫码。签名与 :func:`qr_api` 相同。

    在事件循环内调用时请用本函数——:func:`qr_api` 是同步的，
    直接在 async 函数里调用会阻塞整个事件循环。
    """
    timestamp, body = _build_request(qr_code)
    logger.debug("扫码请求 timestamp=%s", timestamp)

    kwargs: dict[str, Any] = {"timeout": config.REQUEST_TIMEOUT}
    if config.PROXY_URL:
        kwargs["proxy"] = config.PROXY_URL
    if config.INSECURE_TLS:
        kwargs["verify"] = False

    try:
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.post(
                config.AIME_QR_URL,
                content=json.dumps(body, separators=(",", ":")),
                headers=_headers(),
            )
    except httpx.HTTPError as exc:
        raise QrApiError(f"扫码接口网络错误：{exc}") from exc
    return _parse_response(response)
