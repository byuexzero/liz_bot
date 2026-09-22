"""maimai 业务接口客户端。

以 eaquira 的 ``MaimaiClient`` 为主干，补上三处它缺的能力：

1. **重试** —— eaquira 一次失败就返回 ``None``；本实现按配置重试。
2. **代理** —— 从 ``.env`` 的 ``PROXY_URL`` 读取，支持 ``http://`` 与 ``socks5://``。
3. **异常** —— eaquira 静默返回 ``None``，会把网络故障伪装成"没有数据"；
   本实现抛 :class:`MaimaiApiError`，让调用方能区分。

请求流程与旧实现完全一致::

    dict ──json.dumps──▶ zlib 压缩 ──AES-CBC 加密──▶ POST
    响应 ──AES-CBC 解密──▶ zlib 解压 ──json.loads──▶ dict
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Mapping

import httpx

from . import config
from .encryption import maimai as maimai_crypto

__all__ = ["MaimaiClient", "MaimaiApiError"]

logger = logging.getLogger(__name__)


class MaimaiApiError(RuntimeError):
    """业务接口调用失败。"""

    def __init__(
        self,
        api_name: str,
        user_id: int | None,
        message: str,
        status_code: int | None = None,
    ) -> None:
        self.api_name = api_name
        self.user_id = user_id
        self.status_code = status_code
        super().__init__(
            f"{api_name}（userId={user_id}）调用失败：{message}"
            + (f" [HTTP {status_code}]" if status_code else "")
        )


def build_httpx_kwargs() -> dict[str, Any]:
    """把 .env 里的网络配置翻译成 httpx 参数。"""
    kwargs: dict[str, Any] = {"timeout": config.REQUEST_TIMEOUT}
    if config.PROXY_URL:
        kwargs["proxy"] = config.PROXY_URL
    if config.INSECURE_TLS:
        kwargs["verify"] = False
    return kwargs


class MaimaiClient:
    """舞萌 DX 业务接口客户端。

    可以自己管一个 ``httpx.AsyncClient``，也可以由调用方传入以复用连接池::

        client = MaimaiClient()
        async with client:
            data = await client.request("GetUserPreviewApi", {...}, user_id=12345678)

    或复用外部连接池（eaquira 的原始用法）::

        async with httpx.AsyncClient() as http:
            data = await client.call_api(http, "GetUserPreviewApi", {...}, 12345678)
    """

    def __init__(
        self,
        base_url: str | None = None,
        proxy: str | None = None,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> None:
        self.base_url = (base_url or config.MAIMAI_BASE_URL).rstrip("/") + "/"
        self.proxy = config.PROXY_URL if proxy is None else proxy
        self.timeout = config.REQUEST_TIMEOUT if timeout is None else timeout
        self.retries = config.REQUEST_RETRIES if retries is None else retries
        self._client: httpx.AsyncClient | None = None
        self._sync_client: httpx.Client | None = None

    # ---- 生命周期 -------------------------------------------------------
    async def __aenter__(self) -> "MaimaiClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            kwargs: dict[str, Any] = {"timeout": self.timeout}
            if self.proxy:
                kwargs["proxy"] = self.proxy
            if config.INSECURE_TLS:
                kwargs["verify"] = False
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ---- 核心调用 -------------------------------------------------------
    def _headers(self, api_hash: str, user_id: int) -> dict[str, str]:
        return {
            "User-Agent": f"{api_hash}#{user_id}",
            "Content-Type": "application/json",
            "Mai-Encoding": config.MAIMAI_ENCODING,
            "Accept-Encoding": "",
            "Charset": "UTF-8",
            "Content-Encoding": "deflate",
            "Expect": "100-continue",
        }

    def _build_request(
        self, api_name: str, data: Mapping[str, Any], user_id: int
    ) -> tuple[str, dict[str, str], bytes]:
        """构造 ``(url, headers, body)``。同步与异步两条路径共用。"""
        api_hash = maimai_crypto.api_hash(api_name)
        url = f"{self.base_url}{api_hash}"
        body = maimai_crypto.pack(json.dumps(dict(data), ensure_ascii=False))
        return url, self._headers(api_hash, user_id), body

    async def call_api(
        self,
        client: httpx.AsyncClient,
        api_name: str,
        data: Mapping[str, Any],
        user_id: int,
    ) -> dict[str, Any]:
        """用指定的 ``httpx.AsyncClient`` 调用接口。

        签名与 eaquira 的 ``MaimaiClient.call_api`` 一致。
        失败时抛 :class:`MaimaiApiError`（eaquira 是返回 ``None``）。

        :raises MaimaiApiError: 网络错误、HTTP 非 2xx、或响应无法解析
        """
        url, headers, body = self._build_request(api_name, data, user_id)

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                delay = min(2 ** (attempt - 1), 8)
                logger.warning(
                    "%s 第 %d 次重试（%.1fs 后）", api_name, attempt, delay
                )
                await asyncio.sleep(delay)
            try:
                response = await client.post(
                    url, headers=headers, content=body, timeout=self.timeout
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = MaimaiApiError(
                    api_name,
                    user_id,
                    f"HTTP 错误 {exc.response.status_code}",
                    exc.response.status_code,
                )
                continue
            except httpx.HTTPError as exc:
                last_error = MaimaiApiError(api_name, user_id, f"网络错误 {exc}")
                continue

            try:
                text = maimai_crypto.unpack(response.content)
                return json.loads(text)
            except Exception as exc:
                raise MaimaiApiError(
                    api_name, user_id, f"响应解析失败：{exc}"
                ) from exc

        raise last_error or MaimaiApiError(api_name, user_id, "未知错误")

    def call_api_sync(
        self,
        api_name: str,
        data: Mapping[str, Any],
        user_id: int,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """同步调用接口。供批量爬虫这类线程池场景使用。

        逻辑与 :meth:`call_api` 完全一致，只是换成同步 HTTP。
        不传 ``client`` 时复用实例内缓存的连接池（用 :meth:`close_sync` 释放）。
        """
        url, headers, body = self._build_request(api_name, data, user_id)
        http = client or self._ensure_sync_client()

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                delay = min(2 ** (attempt - 1), 8)
                logger.warning(
                    "%s 第 %d 次重试（%.1fs 后）", api_name, attempt, delay
                )
                time.sleep(delay)
            try:
                response = http.post(
                    url, headers=headers, content=body, timeout=self.timeout
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = MaimaiApiError(
                    api_name,
                    user_id,
                    f"HTTP 错误 {exc.response.status_code}",
                    exc.response.status_code,
                )
                continue
            except httpx.HTTPError as exc:
                last_error = MaimaiApiError(api_name, user_id, f"网络错误 {exc}")
                continue

            try:
                text = maimai_crypto.unpack(response.content)
                return json.loads(text)
            except Exception as exc:
                raise MaimaiApiError(
                    api_name, user_id, f"响应解析失败：{exc}"
                ) from exc

        raise last_error or MaimaiApiError(api_name, user_id, "未知错误")

    def _ensure_sync_client(self) -> httpx.Client:
        if self._sync_client is None or self._sync_client.is_closed:
            kwargs: dict[str, Any] = {"timeout": self.timeout}
            if self.proxy:
                kwargs["proxy"] = self.proxy
            if config.INSECURE_TLS:
                kwargs["verify"] = False
            self._sync_client = httpx.Client(**kwargs)
        return self._sync_client

    def close_sync(self) -> None:
        """释放同步连接池。"""
        if self._sync_client is not None and not self._sync_client.is_closed:
            self._sync_client.close()
        self._sync_client = None

    async def request(
        self,
        api_name: str,
        data: Mapping[str, Any],
        user_id: int | None = None,
    ) -> dict[str, Any]:
        """便捷调用，自动管理连接池。

        :param user_id: 省略时取 ``.env`` 中的 ``USER_ID``
        """
        uid = config.USER_ID if user_id is None else user_id
        if not uid:
            raise MaimaiApiError(api_name, None, "未提供 userId，且 .env 中 USER_ID 为空")
        client = await self._ensure_client()
        return await self.call_api(client, api_name, data, uid)
