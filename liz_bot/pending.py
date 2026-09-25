"""多轮补参 —— 记住「这个会话正在等用户的下一句话」。

两种等待
--------

同一个状态机承载两类「接住用户下一条消息」的场景：

**1. 补参数**（``choices is None``）—— 参数不够时把下一条消息当参数::

    用户: /bm                 ← 参数不够
    Liz : 还差 1 个参数（别名）～ 直接发给我就好
    用户: 真爱                 ← 纯文本，不是指令
    Liz : <查歌结果>

缺多个参数时可以**分多次补**，参数会**叠加** —— 这就是「多次回复叠加」：

    用户: /添加别名
    Liz : 还差 2 个参数（歌名 别名）～ 直接发给我就好
    用户: 8
    Liz : 还差 1 个参数（别名）～ 直接发给我就好
    用户: 别名
    Liz : <结果>

**2. 选一个**（``choices`` 非空）—— 检索命中多个候选时，把下一条消息当**选择**::

    用户: /bm ジングルベル      ← 这个别名挂在 SD 与 DX 两首上
    Liz : 匹配到 2 首，回复序号或 id 选择：
            1. SD ジングルベル id 70
            2. DX ジングルベル id 10070
    用户: 2                    ← 选 DX
    Liz : <DX 那首的查歌结果>

为什么复用同一个状态机而不是另起一套
------------------------------------
两者的**语义完全一样**（「这条指令还没完成，等用户补一句话」），只是缺的东西
不同：一个缺参数、一个缺「选哪一个」。复用之后 TTL、容量淘汰、会话键、
「指令优先」「超时作废」两条安全阀**全都自动适用**，不必再实现一遍，
也不会出现「补参超时 60 秒、选择超时 300 秒」这种两套节奏。

状态只放在**内存**里，进程重启即失效。这没关系：补参本来就是秒级交互，
持久化反而会带来「昨天没收完的指令今天突然被续上」这种怪事。

三条安全阀
----------
1. **成功才消费** —— 凑够参数（或选定候选）后立刻执行；**成功**就消费掉状态，
   不会长期粘在用户身上。**失败则留住状态**（参数清空）让用户重发一次 ——
   见 ``command_router._resume_pending``。这是 2026-09-26 改的：上一版是
   「失败也退出」，用户输错一个参数就得把整条指令重打一遍，正是补参想省掉的
   那件事。
2. **指令优先** —— 补参期间用户发新指令（``/`` 或 ``#`` 开头）会**取消**补参，
   正常走指令流程。不会出现「想换个指令却被当成参数吞掉」。
3. **超时作废** —— 默认 60 秒没续补就自动丢掉，避免把几分钟后的正常聊天
   误当成参数。

会话键的选取
------------
由调用方给出，本项目用 ``群 openid:成员 openid``（见 ``qqgroupbot._session_key``）。
**必须带群** —— ``member_openid`` 只在同一个群内稳定，只用它会把不同群的
同一个人串在一起。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

#: 补参状态的存活时间（秒）。超时自动作废。
#:
#: 取 60 而不是更长：群里聊天节奏快，超过一分钟还没续补，
#: 下一条消息是「正常聊天」的概率已经远大于「在补参」。
DEFAULT_TTL = 60.0

#: 最多同时记住多少个会话，超出时淘汰最旧的。
#: 纯粹是防内存无上限增长 —— 正常量级是「最近一分钟内输错参数的会话数」。
DEFAULT_CAPACITY = 1000


@dataclass
class Pending:
    """一次「等用户补一句话」的会话。"""

    cmd_key: str
    """指令在 :data:`liz_bot.command_router.COMMANDS` 里的 ``key``。

    存 key 而不是用户输入的别名 —— 用户可能用 ``/别名查歌`` 进来，
    但补参时要按规范名执行，存别名会让两个入口产生两套状态。
    """

    params: list[str]
    """**已经收到**的全部参数（可能还差几个才够 ``min_params``）。

    在「选一个」模式下这里是**触发本次选择的那个关键词**
    （``["ジングルベル"]``），只作调试线索；真正决定结果的是 :attr:`choices`。
    """

    updated_at: float
    """最后一次续补的 :func:`time.monotonic` 时间戳，用于判超时。"""

    choices: list[int] | None = None
    """候选列表 —— **非空表示这次是在「选一个」，而不是在补参数**。

    对查歌类指令存的是**曲目 id**，不是整条乐曲记录：
    候选只是「让用户挑一个」，挑中后再按 id 重新检索即可，
    既不占内存，也避免在状态里留一份可能过期的数据快照。

    ``None`` 表示这次是补参数（见模块文档「两种等待」）。
    """


class PendingStore:
    """按会话键记住「正在补参」的指令。**线程安全**。

    :param ttl: 存活秒数，见 :data:`DEFAULT_TTL`
    :param capacity: 容量上限，见 :data:`DEFAULT_CAPACITY`
    """

    def __init__(self, ttl: float = DEFAULT_TTL, capacity: int = DEFAULT_CAPACITY):
        self.ttl = ttl
        self.capacity = capacity
        self._items: dict[str, Pending] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _sweep(self, now: float) -> None:
        """清掉已过期的条目。**调用方必须已持锁**。"""
        dead = [k for k, p in self._items.items() if now - p.updated_at > self.ttl]
        for key in dead:
            del self._items[key]

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    def get(self, key: str | None) -> Pending | None:
        """取某会话的补参状态；已超时（或没有）返回 ``None``。"""
        if not key:
            return None
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if now - item.updated_at > self.ttl:
                del self._items[key]
                return None
            return item

    def put(
        self,
        key: str | None,
        cmd_key: str,
        params: list[str],
        choices: list[int] | None = None,
    ) -> None:
        """写入 / 续写等待状态。

        :param params: **已经收集到的全部**参数（不是本次增量）——
            调用方负责叠加，这里只做存储，避免「增量还是全量」的歧义。
        :param choices: 候选列表。给了就是「选一个」模式，不给是「补参数」模式。
            空列表按 ``None`` 处理（空候选没有意义，不该切到选择模式）。
        """
        if not key:
            return
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            while len(self._items) >= self.capacity:
                oldest = min(self._items, key=lambda k: self._items[k].updated_at)
                del self._items[oldest]
            self._items[key] = Pending(
                cmd_key=cmd_key,
                params=list(params),
                updated_at=now,
                choices=list(choices) if choices else None,
            )

    def drop(self, key: str | None) -> None:
        """取消某会话的补参。

        三种时机：用户发了新指令、补参凑够参数（已消费）、补参结果不合法。
        """
        if not key:
            return
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        """清空全部（测试用）。"""
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        """当前有效的会话数（顺手清掉过期的）。"""
        with self._lock:
            self._sweep(time.monotonic())
            return len(self._items)
