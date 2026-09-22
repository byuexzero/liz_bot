"""上号工作流。

移植自 eaquira ``sdgb/ticket.py`` 的 ``run_workflow``，改动：

1. **修复 ``time.sleep(60)``** —— 旧版在 ``async def`` 里用同步 sleep，
   会阻塞整个事件循环。改为 ``await asyncio.sleep()``。
2. **上传改为容器驱动** —— 旧版把 ``UpsertUserAllApi`` 的请求体硬编码在
   ``payloads.build_user_all`` 里，``userMusicDetailList`` 直接塞调用方给的那一条
   成绩，``userCharacterList`` / ``userItemList`` 永远是空数组。
   现在改走 :class:`~maimai.user_data.UserData`：拉取 → 装进容器 →
   ``apply_play`` 登记变更 → 只导出**被改动过**的条目。
3. **返回结构化结果** —— 旧版靠 ``logger.error`` 报错后静默 ``return``，
   调用方无法区分"成功"和"被跳过"。现在返回 :class:`WorkflowResult`。

流程::

    扫码取 token ─▶ Preview 探测 ─▶ UserLogin ─▶ 装载用户数据（容器层）
      ─▶ apply_play 登记成绩 ─▶ 等待(模拟游玩时长) ─▶ UpsertUserAll ─▶ UserLogout
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import config, payloads
from .client import MaimaiApiError, MaimaiClient
from .qr import ERROR_ID_EXPIRED, ERROR_ID_SUCCESS, QrApiError, qr_api_async
from .user_data import PlayInput, UserData

__all__ = ["WorkflowResult", "run_workflow", "LOGIN_RETURN_CODE_OK", "LOGIN_RETURN_CODE_CHIME_FAILED"]

logger = logging.getLogger(__name__)

#: UserLoginApi 的 returnCode：1 = 新登录成功
LOGIN_RETURN_CODE_OK = 1
#: returnCode 106 = chime 验证失败
LOGIN_RETURN_CODE_CHIME_FAILED = 106


@dataclass
class WorkflowResult:
    """工作流执行结果。

    :param ok: 是否完整走完流程
    :param stage: 结束时所处的阶段名
    :param message: 人类可读的说明
    :param stages: 已完成的阶段列表，便于排查卡在哪一步
    :param modified_count: 本次上传涉及的容器变更条数
    :param user_data: 装载完成的 :class:`~maimai.user_data.UserData`，
        流程失败时也可能有值（例如卡在 UpsertUserAll）
    """

    ok: bool
    stage: str
    message: str
    user_id: int | None = None
    login_id: int | None = None
    preview: dict[str, Any] | None = None
    login: dict[str, Any] | None = None
    stages: list[str] = field(default_factory=list)
    modified_count: int = 0
    user_data: UserData | None = None

    def __str__(self) -> str:
        mark = "OK" if self.ok else "FAIL"
        return f"[{mark}] {self.stage}: {self.message}"


async def _resolve_credentials(
    qr_code: str | None, token: str | None, user_id: int | None
) -> tuple[int, str, list[str]]:
    """取得 ``(user_id, token, stages)``。

    给了 ``qr_code`` 就扫码换取；否则用传入/配置里的 ``token``。
    """
    stages: list[str] = []

    if qr_code:
        response = await qr_api_async(qr_code)
        error_id = response.get("errorID")
        if error_id == ERROR_ID_EXPIRED:
            raise QrApiError("二维码已过期，请重新扫码")
        if error_id != ERROR_ID_SUCCESS:
            raise QrApiError(f"扫码失败，errorID={error_id}")
        resolved_user_id = int(response["userID"])
        resolved_token = response["token"]
        stages.append("扫码")
        logger.info("扫码成功，userId=%s", resolved_user_id)
        return resolved_user_id, resolved_token, stages

    resolved_user_id = user_id or config.USER_ID
    resolved_token = token or ""
    if not resolved_user_id:
        raise ValueError("未提供 qr_code，且 user_id / .env 的 USER_ID 均为空")
    if not resolved_token:
        raise ValueError("未提供 qr_code 时，必须显式传入 token")
    stages.append("使用已有凭据")
    return resolved_user_id, resolved_token, stages


async def run_workflow(
    music_data: Mapping[str, Any],
    *,
    qr_code: str | None = None,
    token: str | None = None,
    user_id: int | None = None,
    play_duration: float = 60.0,
    include_items: bool = True,
    client: MaimaiClient | None = None,
) -> WorkflowResult:
    """执行一次完整上号流程。

    :param music_data: 本次游玩的曲目成绩，至少含 ``musicId`` / ``level`` /
        ``achievement`` / ``comboStatus`` / ``syncStatus`` / ``deluxscoreMax``。
        会被 :meth:`UserData.apply_play` 写进成绩容器，
        ``playCount`` 在原有基础上 ``+1``，``scoreRank`` 由达成率推导
        （传入的 ``scoreRank`` 会被忽略）。
    :param qr_code: 扫卡二维码。给了就自动换取 userId 与 token。
    :param token: 与 ``user_id`` 搭配使用，跳过扫码
    :param user_id: 省略时取 ``.env`` 的 ``USER_ID``
    :param play_duration: 模拟游玩时长（秒），旧版固定 60。
        **本参数改为 await，不再阻塞事件循环。**
    :param include_items: 是否拉取道具/礼物（请求数较多，可关掉提速）
    :param client: 可选的 :class:`MaimaiClient`，便于复用连接池

    :returns: :class:`WorkflowResult`
    """
    owns_client = client is None
    maimai = client or MaimaiClient()
    stages: list[str] = []
    user_data: UserData | None = None

    try:
        # --- 1. 凭据 ---------------------------------------------------
        resolved_user_id, resolved_token, credential_stages = (
            await _resolve_credentials(qr_code, token, user_id)
        )
        stages.extend(credential_stages)

        # --- 2. Preview 探测 -------------------------------------------
        preview = await maimai.request(
            "GetUserPreviewApi",
            payloads.build_user_preview(resolved_user_id, resolved_token),
            resolved_user_id,
        )
        stages.append("Preview")
        if preview.get("isLogin"):
            logger.error("userId=%s 已在他处登录", resolved_user_id)
            return WorkflowResult(
                ok=False,
                stage="Preview",
                message="账号已在他处登录，请先登出",
                user_id=resolved_user_id,
                preview=preview,
                stages=stages,
            )

        # --- 3. UserLogin ----------------------------------------------
        login = await maimai.request(
            "UserLoginApi",
            payloads.build_user_login(resolved_user_id, resolved_token),
            resolved_user_id,
        )
        stages.append("UserLogin")

        return_code = login.get("returnCode")
        if return_code == LOGIN_RETURN_CODE_CHIME_FAILED:
            logger.error("userId=%s chime 验证失败", resolved_user_id)
            return WorkflowResult(
                ok=False,
                stage="UserLogin",
                message="chime 验证失败（returnCode=106）",
                user_id=resolved_user_id,
                preview=preview,
                login=login,
                stages=stages,
            )
        if return_code not in (LOGIN_RETURN_CODE_OK, 100, 102):
            logger.error("userId=%s 登录失败 returnCode=%s", resolved_user_id, return_code)
            return WorkflowResult(
                ok=False,
                stage="UserLogin",
                message=f"登录失败，returnCode={return_code}",
                user_id=resolved_user_id,
                preview=preview,
                login=login,
                stages=stages,
            )

        login_id = login["loginId"]
        login_date = login["lastLoginDate"]
        login_date_time = int(login["loginDateTime"])

        # --- 4. 装载用户数据（容器层） ----------------------------------
        user_data = await UserData.load(
            maimai, resolved_user_id, include_items=include_items
        )
        stages.append("装载用户数据")

        # --- 5. 登记本次成绩 -------------------------------------------
        play = PlayInput.from_mapping(music_data)
        user_data.apply_play(play)
        stages.append("登记成绩")

        # --- 6. 模拟游玩时长 -------------------------------------------
        # 旧版这里写的是 time.sleep(60)，在 async 函数里会卡死事件循环。
        if play_duration > 0:
            logger.info("等待 %.0f 秒模拟游玩时长…", play_duration)
            await asyncio.sleep(play_duration)
            stages.append("等待游玩时长")

        # --- 7. UpsertUserAll（容器增量导出） ---------------------------
        payload = user_data.build_upsert(
            login_id=login_id,
            login_date=login_date,
            login_date_time=login_date_time,
            plays=[play],
        )
        await maimai.request("UpsertUserAllApi", payload, resolved_user_id)
        stages.append("UpsertUserAll")

        modified_count = user_data.modified_count

        # --- 8. UserLogout ---------------------------------------------
        await maimai.request(
            "UserLogoutApi",
            payloads.build_user_logout(resolved_user_id, login_date_time),
            resolved_user_id,
        )
        stages.append("UserLogout")

        logger.info(
            "userId=%s 流程完成（上传 %d 条变更）", resolved_user_id, modified_count
        )
        return WorkflowResult(
            ok=True,
            stage="完成",
            message=f"上号流程已完整执行，上传 {modified_count} 条容器变更",
            user_id=resolved_user_id,
            login_id=login_id,
            preview=preview,
            login=login,
            stages=stages,
            modified_count=modified_count,
            user_data=user_data,
        )

    except (MaimaiApiError, QrApiError, ValueError) as exc:
        logger.error("流程在 %s 阶段中断：%s", stages[-1] if stages else "初始化", exc)
        return WorkflowResult(
            ok=False,
            stage=stages[-1] if stages else "初始化",
            message=str(exc),
            stages=stages,
            user_data=user_data,
        )
    finally:
        if owns_client:
            await maimai.aclose()
