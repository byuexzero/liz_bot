"""AI 聊天黑名单 —— 名单上的人不能和 Liz 聊天（**但指令照常可用**）。

为什么需要
----------
群里总有不想让 AI 接话的人：刷屏的、反复触发内容审核的、或者单纯不想理。
拉黑这件事有三个约束，缺一个就会做成「看着配好了、其实没生效」：

1. **不改代码、不重启** —— 名单是**运行时数据**，改完下一条消息生效。
   要 SSH 上去改代码再部署才能拉黑一个人，实际上就等于没法用。
2. **不进 git** —— 仓库是 **public**，谁被拉黑不该被公开。
3. **指令照常** —— 这是「不和 Liz 聊天」，不是「封禁这个人」。
   ``/help``、``/估分`` 该能用还得能用。

放在哪
------
``<AI_CHAT_DIR>/blacklist.txt`` —— 数据卷上，与好感度同源（见
:mod:`liz_bot.runtime_paths`）。文件不存在时会**自动生成一份带说明的空模板**，
运维 SSH 上去就能看到格式，不用翻代码。

格式
----
一行一条；``#`` 开头是注释；空行忽略。支持两种写法：

* **成员 openid** —— 该人在**所有**群里都被拉黑::

      88B9028

* **完整会话键** —— 只拉黑**这一个群**里的这个人::

      65E8F7FB...:88B9028

⚠️⚠️ **不能填 QQ 号。** QQ 开放平台**不向机器人暴露 QQ 号**（隐私设计）——
群消息里只有 ``member_openid``（形如 ``65E8F7FB9EA66138D9B3F8B913B65D96_88B9028``）。
填纯数字会在加载时打一条 ``WARNING``。这是最容易踩的坑：
**名单看着配好了，实际一条都匹配不上，而且不会有任何报错。**

怎么拿到 openid
---------------
:mod:`liz_bot.ai_chat` 每次调用都会在日志里带上成员 openid::

    AI 用量：输入 1243 / 输出 6 token（其中思维链 0）· 成员 88B9028

让对方 @ 一次 Liz，然后从日志里取那个值。
"""

from __future__ import annotations

import os
import re
import threading

from botpy import logging

from liz_bot.runtime_paths import BLACKLIST_FILE

_log = logging.get_logger()

#: 纯数字、5~12 位 ⇒ 极可能是 QQ 号（openid 不是这个形状）。
#: 用它来**提前报警**，而不是安静地匹配不上。
_QQ_LIKE_RE = re.compile(r"^\d{5,12}$")

#: 首次运行时写入的模板 —— 让运维不用翻代码就知道格式。
_TEMPLATE = """\
# AI 聊天黑名单 —— 一行一条，`#` 开头是注释，空行忽略。
#
# 支持两种写法：
#   成员 openid          该人在所有群里都不能和 Liz 聊天
#   群openid:成员openid   只拉黑这一个群里的这个人
#
# ⚠️⚠️ 不能填 QQ 号！
#    QQ 开放平台不向机器人暴露 QQ 号，群里消息只带 member_openid
#    （形如 65E8F7FB9EA66138D9B3F8B913B65D96_88B9028）。
#    填纯数字会匹配不上，而且不会有任何报错。
#
# 怎么拿到 openid：
#    让对方 @ 一次 Liz，然后看日志里这一行的末尾 ——
#        AI 用量：输入 1243 / 输出 6 token（其中思维链 0）· 成员 <openid>
#
# 改完**不用重启**（mtime 热更新），下一条消息生效。
"""

_lock = threading.Lock()

#: ``(mtime, 名单)``。mtime 变了才重新读盘。
_cache: tuple[float, frozenset[str]] | None = None


def _ensure_template() -> None:
    """文件不存在时写一份带说明的空模板。

    ⚠️ **失败就算了** —— 拉黑是附加功能，不该因为磁盘只读就让机器人起不来。
    """
    try:
        if os.path.exists(BLACKLIST_FILE):
            return
        os.makedirs(os.path.dirname(BLACKLIST_FILE), exist_ok=True)
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as fh:
            fh.write(_TEMPLATE)
        _log.info("已生成 AI 黑名单模板：%s", BLACKLIST_FILE)
    except OSError as exc:
        _log.warning("AI 黑名单模板生成失败：%s（%s）", BLACKLIST_FILE, exc)


def _parse(raw: str) -> frozenset[str]:
    """解析名单文本。**调用方负责打日志**（这里只做纯解析，便于自检）。"""
    out: set[str] = set()
    for line in raw.splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            out.add(entry)
    return frozenset(out)


def _warn_qq_like(entries: frozenset[str]) -> None:
    """名单里出现「疑似 QQ 号」时报警 —— 它一定匹配不上，但不报警就没人知道。"""
    for entry in sorted(entries):
        if _QQ_LIKE_RE.match(entry):
            _log.warning(
                "⚠️ AI 黑名单里有一条疑似 QQ 号：%r —— **匹配不上**。"
                "需要的是 member_openid（格式见 %s 的开头说明）",
                entry,
                BLACKLIST_FILE,
            )


def _entries() -> frozenset[str]:
    """当前名单（mtime 热更新）。**任何读取失败都当作空名单**。"""
    global _cache

    _ensure_template()
    try:
        mtime = os.stat(BLACKLIST_FILE).st_mtime
    except OSError:
        return frozenset()

    with _lock:
        if _cache is not None and _cache[0] == mtime:
            return _cache[1]

    try:
        with open(BLACKLIST_FILE, encoding="utf-8") as fh:
            parsed = _parse(fh.read())
    except OSError as exc:
        _log.warning("AI 黑名单读取失败：%s（%s）", BLACKLIST_FILE, exc)
        return frozenset()

    with _lock:
        _cache = (mtime, parsed)
    _log.info("AI 黑名单已载入：%s 条（%s）", len(parsed), BLACKLIST_FILE)
    _warn_qq_like(parsed)
    return parsed


def is_blocked(session_key: str | None) -> bool:
    """这个会话是否被拉黑。

    两条判据，命中任一即算：

    * **完整会话键**（``群:成员``）在名单里 ⇒ 只拉黑这一个群；
    * **成员 openid** 在名单里 ⇒ 所有群都拉黑。

    ⚠️ 拿不到会话键时返回 ``False`` —— 宁可漏放。反过来（默认拉黑）会因为
    一个缺失字段把所有人的消息都吞掉，而且**不报错**。
    """
    if not session_key:
        return False

    entries = _entries()
    if not entries:
        return False
    if session_key in entries:
        return True
    return session_key.rsplit(":", 1)[-1] in entries


def entries() -> frozenset[str]:
    """当前名单（运维自检用）。"""
    return _entries()


def reset_cache() -> None:
    """丢掉 mtime 缓存。**测试用** —— 改完文件要立刻生效时也可以调。"""
    global _cache
    with _lock:
        _cache = None
