"""群聊**主动推送** —— 让 Liz 自己开口（2026-09-29）。

⚠️⚠️ **和「被动回复」不是一回事，动它之前先看清楚**：

============  ===============================  ============================
类型          带 ``msg_id`` 吗                  需要什么
============  ===============================  ============================
被动回复      带（来自事件的 ``d.id``）         **不需要任何权限**，5 分钟 / 5 次
主动推送      **不带**                         **群主必须开一个开关**（见下）
============  ===============================  ============================

那个开关在**手机 QQ 的群设置里**，**只有群主能改**：

    群聊 → 设置 → 机器人 → 打开「机器人主动在群聊内发言」

没开 ⇒ 网关直接返 ``40034105 主动消息失败, 无权限``（2026-09-29 实测就是这个码）。

⚠️ **别被 2025 年的老帖子带偏**：官方 2025-04-21 确实停过一次主动推送，
但 **2026-06-22 群场景主动推送能力已全量开放**，只是多了「群主开开关」这个前提。
（顺带：把同一个设置页里的「机器人可获取的群聊消息范围」设成「获取群内全部消息」，
机器人就能收到**非 @ 的群消息** —— 那是 ``GROUP_MSG_RECEIVE`` 事件，另一条路。）
官方口径见 ``bot.q.qq.com/wiki/agent-qqbot/``。

⚠️⚠️ **本模块默认完全不启动**：``LIZ_PROACTIVE_TIMES`` 与
``LIZ_PROACTIVE_GROUPS`` **任一为空**就不起定时器。理由是这两件事都不该在
「没配」的时候发生 —— 权限没开时它只会每个时间点在日志里刷一行失败；
权限开了却没人盯着，它就会真的往群里发话。

⚠️ 还有一条**官方原话的静默失败**：群成员可以在 QQ 客户端关掉「接收主动消息」，
关掉之后主动消息**一律发送失败**。⇒ 主动推送是**尽力而为**，
**不要**拿它做「一定要送达」的事。

频控（官方接口文档，2026-09-29 抄录）：Bot 维度 60 qpm（已认证）/ 30 qpm（未认证）；
单关系维度 20 qpm、每个群 1 天最多接收 1000 条。
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

from botpy import logging

from liz_bot import ai_chat

_log = logging.get_logger()

#: 固定 **UTC+8**，**刻意不用** ``ZoneInfo("Asia/Shanghai")``。
#:
#: 两个理由：① 服务器 / 容器大概率跑在 UTC（腾讯云轻量默认就是），用
#: ``datetime.now()`` 会让「21:00 推」变成北京时间凌晨五点；② ``ZoneInfo``
#: 依赖系统 tzdata，精简镜像里经常没有，缺了会抛异常 —— 而这里根本用不上
#: 夏令时（中国没有）。固定偏移是**没有依赖且永远正确**的那个选择。
_TZ = timezone(timedelta(hours=8))

#: 轮询间隔（秒）。
#:
#: 定 30 而不是 60：协程调度会让 tick 稍微漂移，60 秒的间隔在「正好卡整分」
#: 时可能整个错过那一分钟。30 秒则必然在同一分钟内命中两次，再由
#: :func:`due` 的去重把第二次挡掉。
TICK_SECONDS = 30.0

_ENV_TIMES = "LIZ_PROACTIVE_TIMES"
_ENV_GROUPS = "LIZ_PROACTIVE_GROUPS"

#: 正在跑的定时器。**必须留引用** —— ``asyncio`` 只持弱引用，
#: 不存下来的话任务可能被 GC 掉，表现为「定时推送毫无征兆地不再触发」。
_TASK: "asyncio.Task[None] | None" = None


def _parse_times(raw: str) -> tuple[tuple[int, int], ...]:
    """``"21:00, 12:30"`` → ``((21, 0), (12, 30))``。

    **非法项跳过并告警**，不抛异常 —— 配错一个时间点不该让机器人起不来。
    中文逗号也认（``，``），因为运维手写时很自然会敲它。
    """
    out: list[tuple[int, int]] = []
    for item in (raw or "").replace("，", ",").split(","):
        item = item.strip()
        if not item:
            continue
        hour_text, _, minute_text = item.partition(":")
        if hour_text.strip().isdigit() and minute_text.strip().isdigit():
            hour, minute = int(hour_text), int(minute_text)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                out.append((hour, minute))
                continue
        _log.warning("主动推送：时间 %r 无法识别（要 HH:MM），已忽略", item)
    return tuple(dict.fromkeys(out))


def _parse_groups(raw: str) -> tuple[str, ...]:
    """群 openid 列表；**去重且保序**。"""
    items = (g.strip() for g in (raw or "").replace("，", ",").split(","))
    return tuple(dict.fromkeys(g for g in items if g))


def times() -> tuple[tuple[int, int], ...]:
    """配置的推送时间点（每次现读环境变量，便于自检改完就生效）。"""
    return _parse_times(os.environ.get(_ENV_TIMES, ""))


def groups() -> tuple[str, ...]:
    """配置的推送目标群。"""
    return _parse_groups(os.environ.get(_ENV_GROUPS, ""))


def enabled() -> bool:
    """**两个环境变量都配了**才算开。任一为空 ⇒ 完全不启动。"""
    return bool(times()) and bool(groups())


def due(
    now: datetime,
    at: tuple[tuple[int, int], ...],
    target_groups: tuple[str, ...],
    sent: set[tuple[str, str, str]],
) -> list[str]:
    """这一刻该推哪些群，**并顺手记进 ``sent``**（防同一分钟推两次）。

    纯函数（除了改 ``sent``），所以自检能直接断言 —— 定时逻辑最怕
    「说好一天一次结果发了两次」，那种 bug 没有自检就只能等群友投诉。

    ``sent`` 的键是 ``(日期, HH:MM, 群)``：跨天自然失效，
    不用清理；进程重启后从头开始，但因为只在**当前这一分钟**触发，
    重启也不会补发当天已经过去的时点。

    :param now: 当前时间（**必须已经带 +08:00 时区**，见 :data:`_TZ`）。
    :returns: 该推送的群 openid 列表（可能为空）。
    """
    if (now.hour, now.minute) not in at:
        return []

    stamp = f"{now.hour:02d}:{now.minute:02d}"
    day = now.strftime("%Y-%m-%d")
    out: list[str] = []
    for group in target_groups:
        key = (day, stamp, group)
        if key in sent:
            continue
        sent.add(key)
        out.append(group)
    return out


def _short(group: str) -> str:
    """日志里只留群 openid 前 8 位 —— 完整值没必要进日志。"""
    return (group or "")[:8] + "…"


async def _push_one(push, group: str) -> None:
    """生成 + 推送一个群。**任何一步失败都只记日志、不抛。**

    ⚠️ 必须在这里就把异常兜住，不能指望外层 —— :func:`due` **已经**把
    这一分钟的名额记进 ``sent`` 了，若这里抛出去，外层捕获后剩下的群
    在本分钟**再也不会被重试**（``due`` 会认为已推过），等于静默少推几个群。
    """
    try:
        text = await ai_chat.proactive_line()
        if not text:
            _log.warning("主动推送：生成失败，跳过群 %s", _short(group))
            return
        ok = await push(group, text)
        _log.info("主动推送%s：群 %s", "成功" if ok else "失败", _short(group))
    except Exception:
        _log.exception("主动推送异常（群 %s）", _short(group))


async def run_loop(push) -> None:
    """后台协程：到点就往每个群推一条。**永不退出、永不抛异常。**

    :param push: ``async (group_openid, text) -> bool``，由
        :meth:`liz_bot.qqgroupbot.MyClient.push_proactive` 提供。
        ⚠️ 刻意**以参数传入**而不是 import —— ``qqgroupbot`` 已经 import 了
        本模块，反过来再 import 就是循环依赖。
    """
    at, target_groups = times(), groups()
    _log.info(
        "主动推送已启动：每天 %s，%d 个群",
        "、".join(f"{h:02d}:{m:02d}" for h, m in at),
        len(target_groups),
    )

    sent: set[tuple[str, str, str]] = set()
    while True:
        try:
            for group in due(datetime.now(_TZ), at, target_groups, sent):
                await _push_one(push, group)
        except Exception:
            # ⚠️ 必须吞掉：这个循环**没有外层看守**，抛出去就是永久静默停摆
            #    （和「AI 不回话」一样，属于最难排查的那类故障）。
            _log.exception("主动推送循环异常（已吞掉，循环继续）")
        await asyncio.sleep(TICK_SECONDS)


def start(push) -> None:
    """在 ``on_ready`` 里调用。**未配置 ⇒ 连任务都不建。**

    ⚠️ 必须防重复启动：gateway 心跳重连会**再次触发 ``on_ready``**，
    不挡的话每重连一次就多一个定时器，推送条数成倍增长。
    """
    global _TASK

    if not enabled():
        _log.info(
            "主动推送未配置（%s / %s 为空），不启动定时器", _ENV_TIMES, _ENV_GROUPS
        )
        return
    if _TASK is not None and not _TASK.done():
        _log.info("主动推送定时器已在运行，忽略重复启动")
        return

    _TASK = asyncio.create_task(run_loop(push))
