"""把运行时生成的图片送进 QQ 群聊 —— 富媒体（分片）上传。

为什么需要它
============
botpy 的 ``post_group_file`` **只接受公网 URL**（由腾讯服务器去拉取）。
我们的判定图是**运行时生成**的，没有公网地址；而把容器的 8080 暴露到公网
会推翻现有部署的安全设计 —— ``deploy/docker-compose.yml`` 刻意只绑
``127.0.0.1``，``DEPLOY_CN.md`` 也写明「公网入站**完全不需要**」。

官方为此提供了**分片上传**（``/files`` 的文档里明确写着「推荐使用分片上传」），
全程**不需要公网地址**：

1. ``POST /v2/groups/{gid}/upload_prepare`` → ``upload_id`` + 各分片的预签名 URL
2. 逐片 ``PUT <presigned_url>`` —— 预签名地址，**不能带鉴权头**
3. 逐片 ``POST /v2/groups/{gid}/upload_part_finish`` 通知该片完成
4. ``POST /v2/groups/{gid}/files`` 带上 ``upload_id`` 合并 → ``file_info``
5. 调用方用 ``msg_type=7`` + ``media.file_info`` 把图发出去

第 1/3/4 步都走 **botpy 自己的 http 客户端**（``api._http``），复用它已维护好的
access_token、超时与重试，不必再单独取一次 token（``pic_haddler`` 那种自己
取 token 的做法会多一份凭据缓存要维护）。只有第 2 步的预签名 PUT 用独立
session —— 那是 COS 地址，带上 ``Authorization`` 反而会被拒。

.. note::

   ``api._http`` 是 botpy 的私有成员，且 botpy 1.2.1（当前最新）**没有**封装
   ``upload_prepare`` / ``upload_part_finish``。这里直接用它的私有 http 客户端，
   是为了**复用鉴权**而不是复制一份 token 逻辑。botpy 若将来加上这两个接口，
   应当改回公开 API。

.. warning::

   本模块**只在真实环境可用**（需要有效的 appid/secret 与真实群）。本地无法
   端到端验证，所以调用方**必须**做好降级 —— 见 ``qqgroupbot._send_rich``。
"""

from __future__ import annotations

import hashlib
import logging

import aiohttp
from botpy.http import Route

logger = logging.getLogger(__name__)

# 注：``aiohttp`` 这里**直接导入、不做容错**，因为它是 ``qq-botpy`` 自己的
# 声明依赖（``Requires: aiohttp, APScheduler, PyYAML``）—— ``botpy/http.py``
# 第 7 行就 import 了它，所以缺 aiohttp 时 botpy 先起不来，这个模块根本没机会
# 被加载。加一层 try/except 只会得到永远走不到的死代码。
# （``judge_image`` 对 PIL 的容错是另一回事：Pillow **不是** botpy 的依赖。）

#: 单次分片 PUT 的超时（秒）。分片通常只有几十 KB，30 秒绰绰有余。
_PUT_TIMEOUT = 30

#: 图片的**软**限制。超过会被降级成「文件」类型（用户要点开才能看），
#: 硬限制是 200MB。我们的图约 60–90 KB，离得很远 —— 这个常量只用来做断言。
IMAGE_SOFT_LIMIT = 20 * 1024 * 1024

#: ``md5_10m`` 字段要的是「文件前 10002432 字节（约 10MB）」的 MD5。
#: 小于 10MB 的文件，它就等于整文件的 md5。
_MD5_10M_SIZE = 10002432

#: 上传成功后 ``file_info`` 的有效期（秒）由服务端给。这个兜底值只在
#: 服务端没返回 ``ttl`` 时用于日志，不影响功能。
_DEFAULT_TTL = 300


def _digests(data: bytes) -> tuple[str, str, str]:
    """返回接口要求的 ``(md5, sha1, md5_10m)``。"""
    return (
        hashlib.md5(data).hexdigest(),
        hashlib.sha1(data).hexdigest(),
        hashlib.md5(data[:_MD5_10M_SIZE]).hexdigest(),
    )


async def upload_group_image(
    api, group_openid: str, data: bytes, filename: str = "judge.png"
) -> dict:
    """把图片送进群聊素材库，返回 ``{"file_info", "file_uuid", "ttl", ...}``。

    :param api: ``client.api``（botpy 的 ``BotAPI``）—— 只用来复用其 http 客户端
    :param group_openid: 群 OpenID
    :param data: 图片字节（PNG/JPG）
    :param filename: 文件名，仅供服务端记录
    :raises RuntimeError: 任一步失败。**调用方负责降级到文字版**
    """
    if not data:
        raise RuntimeError("图片内容为空")

    md5, sha1, md5_10m = _digests(data)

    # ---- 1. 申请上传任务 ----
    prepared = await api._http.request(
        Route(
            "POST",
            "/v2/groups/{group_openid}/upload_prepare",
            group_openid=group_openid,
        ),
        json={
            "file_type": 1,  # 1 = 图片（png/jpg）
            "file_name": filename,
            # ⚠️ 文档里 file_size / block_size 都是 **string**，照抄别转 int
            "file_size": str(len(data)),
            "md5": md5,
            "sha1": sha1,
            "md5_10m": md5_10m,
        },
    )
    if not isinstance(prepared, dict) or not prepared.get("upload_id"):
        raise RuntimeError(f"upload_prepare 返回异常：{prepared!r}")

    upload_id = prepared["upload_id"]
    parts = prepared.get("parts") or []
    if not parts:
        raise RuntimeError(f"upload_prepare 未返回任何分片：{prepared!r}")

    logger.info(
        "开始分片上传：%d 字节 / %d 片，upload_id=%s",
        len(data), len(parts), upload_id,
    )

    # ---- 2/3. 逐片 PUT 到预签名地址，然后通知服务端该片完成 ----
    offset = 0
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=_PUT_TIMEOUT)
    ) as session:
        for part in parts:
            index = part.get("index")
            size = int(part.get("block_size") or prepared.get("block_size") or len(data))
            chunk = data[offset:offset + size]
            offset += size
            if not chunk:
                raise RuntimeError(f"分片 {index} 切片为空（offset={offset} size={size}）")

            # 预签名 URL 自带签名，**不要**加 Authorization —— 加了会被 COS 判为
            # 签名不匹配（403）。这一点与前面几步刚好相反，容易写错。
            async with session.put(part["presigned_url"], data=chunk) as resp:
                if resp.status not in (200, 201, 204):
                    body = (await resp.text())[:200]
                    raise RuntimeError(
                        f"分片 {index} 上传失败：HTTP {resp.status} {body!r}"
                    )

            await api._http.request(
                Route(
                    "POST",
                    "/v2/groups/{group_openid}/upload_part_finish",
                    group_openid=group_openid,
                ),
                json={
                    "upload_id": upload_id,
                    "part_index": index,
                    "block_size": str(size),
                    "md5": hashlib.md5(chunk).hexdigest(),
                },
            )

    if offset != len(data):
        # 服务端给的分片大小加起来和文件对不上 —— 大概率接口行为变了。
        # 这里只告警不中止：合并那一步会以服务端的记录为准，真有问题它会报错。
        logger.warning(
            "分片总长 %d 与文件大小 %d 不一致（upload_id=%s）", offset, len(data), upload_id
        )

    # ---- 4. 合并 ----
    merged = await api._http.request(
        Route("POST", "/v2/groups/{group_openid}/files", group_openid=group_openid),
        json={
            "file_type": 1,
            "srv_send_msg": False,  # False = 只拿 file_info，不占用主动消息频次
            "file_name": filename,
            "upload_id": upload_id,
        },
    )
    if not isinstance(merged, dict) or not merged.get("file_info"):
        raise RuntimeError(f"合并分片失败：{merged!r}")

    logger.info(
        "上传完成：file_uuid=%s ttl=%s",
        merged.get("file_uuid"), merged.get("ttl", _DEFAULT_TTL),
    )
    return merged


async def send_group_image(
    api,
    group_openid: str,
    data: bytes,
    *,
    msg_id: str | None = None,
    msg_seq: int = 1,
    filename: str = "judge.png",
) -> dict:
    """上传图片并**作为被动回复**发到群里。

    带 ``msg_id`` 就是被动回复（不消耗主动消息配额）；这正是我们要的 ——
    用户发了指令，我们在 5 分钟窗口内回一张图。

    :raises RuntimeError: 上传或发送失败。**调用方负责降级到文字版**
    """
    media = await upload_group_image(api, group_openid, data, filename)
    await api.post_group_message(
        group_openid=group_openid,
        msg_type=7,  # 7 = 富媒体
        media={"file_info": media["file_info"]},
        msg_id=msg_id,
        msg_seq=msg_seq,
    )
    return media
