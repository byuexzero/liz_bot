"""简单请求体构造。

移植自 eaquira ``sdgb/payload.py``，三处改动：

1. **去掉 import 期副作用** —— 旧版在模块顶层就调 ``qr_api()`` 发网络请求、
   并冻结 ``TimeStamp``；本实现全部改为函数参数。
2. **userId / 机厅信息来自配置** —— 旧版从 ``settings.py`` 硬编码导入。
3. **不再包含 ``UpsertUserAllApi`` 的请求体** —— 它已经改为容器驱动，
   见 :mod:`maimai.user_data`。这里只保留几个字段固定的小请求体。

另：旧版 ``UserPlaylog_payload`` 在 eaquira 的 1.50→1.53 重构中丢失
（``ticket.py`` 调用它会 ``NameError``），playlog 现在由
:func:`maimai.user_data.generate_playlog` 从成绩容器生成。
"""

from __future__ import annotations

import time
from typing import Any

from . import config

__all__ = [
    "version_number",
    "build_user_preview",
    "build_user_login",
    "build_user_data",
    "build_user_logout",
    "calc_random",
]


def version_number(version: str) -> int:
    """把 ``"1.41.00"`` 形式的版本号转成整数。

    等价于 Lionheart 的 ``convertVersionNumber``::

        1.41.00 -> 1 * 1000000 + 41 * 1000 + 0 = 1041000
        1.53    -> 1053000

    只有两段时会补一个 ``0``，方便直接传 ``MAIMAI_ENCODING``。
    """
    parts = version.split(".")
    if len(parts) == 2:
        parts.append("0")
    if len(parts) != 3:
        raise ValueError(f"版本号格式不正确：{version!r}（期望 major.minor[.patch]）")
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"版本号含非数字段：{version!r}") from exc
    return major * 1000000 + minor * 1000 + patch


def calc_random() -> int:
    """``userGamePlaylog.playSpecial`` 用的伪随机数。

    延迟导入，避免 ``payloads`` 与 ``encryption`` 形成循环依赖。
    """
    from .encryption.maimai import calc_random as _calc_random

    return _calc_random()


# --------------------------------------------------------------------------
# 简单请求体
# --------------------------------------------------------------------------
def build_user_preview(
    user_id: int, token: str, client_id: str | None = None
) -> dict[str, Any]:
    """``GetUserPreviewApi`` 请求体。用于探测是否已在他处登录。"""
    return {
        "userId": user_id,
        "segaIdAuthKey": "",
        "token": token,
        "clientId": client_id or config.CLIENT_ID,
    }


def build_user_login(
    user_id: int,
    token: str,
    timestamp: int | None = None,
    *,
    play_duration: int = 600,
    client_id: str | None = None,
    region_id: int | None = None,
    place_id: int | None = None,
    is_continue: bool = False,
    generic_flag: int = 0,
) -> dict[str, Any]:
    """``UserLoginApi`` 请求体。

    :param play_duration: ``loginDateTime`` 与 ``dateTime`` 的差值（秒），
        模拟本次游玩时长，默认 600。
    """
    now = int(time.time()) if timestamp is None else timestamp
    return {
        "userId": user_id,
        "accessCode": "",
        "regionId": config.REGION_ID if region_id is None else region_id,
        "placeId": config.PLACE_ID if place_id is None else place_id,
        "clientId": client_id or config.CLIENT_ID,
        "dateTime": now - play_duration,
        "loginDateTime": now,
        "isContinue": is_continue,
        "genericFlag": generic_flag,
        "token": token,
    }


def build_user_data(user_id: int) -> dict[str, Any]:
    """``GetUser*Api`` 系列共用的请求体（只有 userId）。"""
    return {"userId": user_id}


def build_user_logout(
    user_id: int,
    login_date_time: int,
    logout_type: int = 1,
    *,
    client_id: str | None = None,
    region_id: int | None = None,
    place_id: int | None = None,
) -> dict[str, Any]:
    """``UserLogoutApi`` 请求体。

    :param logout_type: 1 = 普通登出（``LogoutType.Logout``）
    """
    return {
        "userId": user_id,
        "accessCode": "",
        "regionId": config.REGION_ID if region_id is None else region_id,
        "placeId": config.PLACE_ID if place_id is None else place_id,
        "clientId": client_id or config.CLIENT_ID,
        "loginDateTime": login_date_time,
        "type": logout_type,
    }
