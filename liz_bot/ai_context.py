"""滑动上下文窗口 —— 只保留最近 N 轮对话，让 Liz 能「接得上话」。

为什么必须有窗口（而不是全量历史）
----------------------------------
旧原型（``qqgroup-ai-bot.py``）把**完整历史每轮全发**：聊到第 50 轮时单次输入
可能 20K+ token，费用涨 5~10 倍，最终还会撞上下文上限。
⇒ 见 ``AI聊天可行性调研.md`` §3.4 与 ``Qwen3.7Flash可行性调研.md`` §5 P1。

**窗口不是体验优化，是账单闸门。** 三个上限同时生效：

===========  ==========================  ==========================================
上限          默认值                      防的是
===========  ==========================  ==========================================
``turns``     20 轮（40 条消息）          无限增长
``chars``     5000 字符                   单条消息特别长时把窗口撑爆
``ttl``       3600 秒（60 分钟）          隔天回来还接着上次的话题
===========  ==========================  ==========================================

⚠️ **2026-09-29 从 10 轮 / 4000 字 / 30 分调到现在的值**（用户要求「上下文窗口
可以适当开大」）。注意三者的**实际约束力完全不同**：

* ``turns`` 才是日常的**真正约束** —— 群聊一句十几二十字，20 轮 ≈ 800 字，
  离 ``chars`` 很远。所以**加轮数几乎不花钱**，加的是「接得上话」的能力。
* ``chars`` 只在有人贴长文时才起作用，它是**成本兜底**而不是日常上限。
  ⇒ 调大它之前先看 ``ai_chat.MAX_PROMPT_CHARS`` 的余量，两者会互相挤。
* ``ttl`` 拉长到 60 分钟是因为群聊本来就是断断续续的，30 分钟常常「刚聊热就忘」。

⚠️ **``chars`` 是硬截断，不是「差不多就行」**：百炼是**阶梯计费**，单次输入一旦
超过 32K，**整单**单价涨 3 倍（¥0.2 → ¥0.6 / 百万）。本窗口 × 人设合起来约
5K token，离 32K 很远，但有人发一段长文就能顶上去 —— 所以要有字符上限兜底。

会话键
------
由调用方给出，本项目用 ``群 openid:成员 openid``（见 ``qqgroupbot._session_key``）。
**必须带群** —— 只用 ``member_openid`` 会把不同群里的同一个人串成一条会话
（旧原型的原话是「会话键缺群维度」，见 ``AI聊天可行性调研.md`` P2）。

为什么只放内存
--------------
进程重启即失效。这**是刻意的**：窗口的寿命本来就只有 :data:`DEFAULT_TTL`，
落盘反而要额外承担「文件写坏 / 并发写 / 大小失控」三件事，收益却接近零。
（对比：**好感度**必须落盘 —— 那是跨会话的长期关系，见 ``liz_bot/affinity.py``。）
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

#: 保留多少**轮**（1 轮 = 用户 1 条 + Liz 1 条）。
#:
#: ⚠️ 这是**日常的真正约束**：群聊一句十几二十字，20 轮 ≈ 800 字 / 约 600 token，
#: 远比 :data:`DEFAULT_CHARS` 先到顶。所以加轮数很便宜，加的是「接得上话」。
DEFAULT_TURNS = 20

#: 窗口内所有消息的字符数上限。超出就从**最老的一轮**开始整轮丢弃。
#: CJK 大致 1 字 ≈ 1 token，5000 字约 4K token。
#: ⚠️ 它是**成本兜底**，不是日常上限 —— 见模块 docstring 里三者的分工。
DEFAULT_CHARS = 5000

#: 多久没说话就把窗口清空（秒）。群聊断断续续，30 分钟常常「刚聊热就忘」。
DEFAULT_TTL = 3600.0

#: 最多同时记住多少个会话（防内存无上限增长）。超出时淘汰最旧的。
DEFAULT_CAPACITY = 500


@dataclass
class _Session:
    """一个会话的窗口。``items`` 是 ``(role, content)``，role 为 ``user``/``assistant``。"""

    items: list[tuple[str, str]] = field(default_factory=list)
    updated_at: float = 0.0


class HistoryStore:
    """按会话键保存最近 N 轮对话。**线程安全**。

    :param turns: 保留轮数，见 :data:`DEFAULT_TURNS`
    :param chars: 字符硬上限，见 :data:`DEFAULT_CHARS`
    :param ttl: 空闲超时秒数，见 :data:`DEFAULT_TTL`
    :param capacity: 会话数上限，见 :data:`DEFAULT_CAPACITY`
    """

    def __init__(
        self,
        turns: int = DEFAULT_TURNS,
        chars: int = DEFAULT_CHARS,
        ttl: float = DEFAULT_TTL,
        capacity: int = DEFAULT_CAPACITY,
    ):
        self.turns = turns
        self.chars = chars
        self.ttl = ttl
        self.capacity = capacity
        self._items: dict[str, _Session] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _sweep(self, now: float) -> None:
        """清掉空闲超时的会话。**调用方必须已持锁**。"""
        dead = [k for k, s in self._items.items() if now - s.updated_at > self.ttl]
        for key in dead:
            del self._items[key]

    def _trim(self, session: _Session) -> None:
        """把窗口压回上限内。**按「轮」整对丢弃**，不留下孤儿消息。

        ⚠️ 整对丢弃很重要：只丢一条 user 会让窗口以 ``assistant`` 开头，
        模型会把那条回复误当成「刚说过的话」，语气会飘。
        """
        max_items = self.turns * 2
        while len(session.items) > max_items:
            del session.items[:2]

        while session.items and sum(len(c) for _, c in session.items) > self.chars:
            if len(session.items) >= 2:
                del session.items[:2]
            else:
                del session.items[:1]

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    def get(self, key: str | None) -> list[tuple[str, str]]:
        """取某会话的窗口（**不含**本次要发的新消息）。

        空闲超过 :attr:`ttl` 时视为没有历史（并顺手清掉）。
        """
        if not key:
            return []
        now = time.monotonic()
        with self._lock:
            session = self._items.get(key)
            if session is None:
                return []
            if now - session.updated_at > self.ttl:
                del self._items[key]
                return []
            return list(session.items)

    def append(self, key: str | None, role: str, content: str) -> None:
        """把一条消息追加进窗口。

        ⚠️ 调用方应在**一次成功的往返之后**把 user 与 assistant **成对**追加
        （见 ``ai_chat.reply``）—— 只追加 user 会让窗口里留下一条没人应答的话，
        下一轮模型会以为它没回，重复回答。

        :param role: ``"user"`` 或 ``"assistant"``；其它值直接忽略（防止写脏）。
        """
        if not key or not content:
            return
        if role not in ("user", "assistant"):
            return

        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            session = self._items.get(key)
            # 空闲超时后再说话 ⇒ 从零开始，不接上次的话题
            if session is None or now - session.updated_at > self.ttl:
                session = _Session()
            while len(self._items) >= self.capacity and key not in self._items:
                oldest = min(self._items, key=lambda k: self._items[k].updated_at)
                del self._items[oldest]
            session.items.append((role, content))
            session.updated_at = now
            self._trim(session)
            self._items[key] = session

    def drop(self, key: str | None) -> None:
        """丢掉某会话的窗口（测试与调试用）。"""
        if not key:
            return
        with self._lock:
            self._items.pop(key, None)

    def reap_expired(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """摘掉并返回**刚过期**的会话。**长期记忆的抽取就挂在这里**。

        为什么用「过期」而不是「显式结束」
        ----------------------------------
        群聊没有「会话结束」这个事件 —— 用户说完就走，不会告别。
        唯一的自然终点就是 :data:`DEFAULT_TTL` 到点：60 分钟没人说话，
        这段对话在窗口里已经没有意义了（下一条消息不会再接它）。

        ⚠️ **必须在窗口被清掉之前拿到内容** —— :meth:`append` 里的
        ``_sweep`` 是静默删的，删完就再也抽不出记忆了。所以这个方法
        由调用方在**每次 append 之前**主动调一次，而不是等自动回收。

        :returns: ``[(会话键, [(role, content), …]), …]``。
            只返回**确实有对话内容**的会话（空窗口没必要抽）。
        """
        now = time.monotonic()
        reaped: list[tuple[str, list[tuple[str, str]]]] = []
        with self._lock:
            for key in list(self._items):
                session = self._items[key]
                if now - session.updated_at <= self.ttl:
                    continue
                if session.items:
                    reaped.append((key, list(session.items)))
                del self._items[key]
        return reaped

    def clear(self) -> None:
        """清空全部（测试用）。"""
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        """当前有效的会话数（顺手清掉超时的）。"""
        with self._lock:
            self._sweep(time.monotonic())
            return len(self._items)


#: 进程内唯一的窗口实例。**只有 ai_chat 用它** —— 与 ``pending`` 的存储分开：
#: 那个管「指令还没补完」，这个管「聊天聊到哪了」，生命周期完全不同。
STORE = HistoryStore()
