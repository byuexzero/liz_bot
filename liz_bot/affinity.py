"""好感度 —— 每个会话一个 ``.txt``，记住 Liz 跟这个人处得怎么样。

沿用旧原型的**「每用户一个 txt」**做法（``qqgroup-ai-bot.py`` 把会话写在
``AI_CHAT_DIR/<member_openid>.txt``），但修掉它的三个问题：

========================  ==============================  ====================
旧原型的做法                这里                          为什么
========================  ==============================  ====================
``eval()`` 反序列化        ``key=value`` 逐行解析         本地文件被改写即可执行任意代码
文件名只有 ``member_openid``  ``群:成员`` 的清洗名 + 短哈希   同一人在不同群会「串台」
整个会话历史写进 txt         只写好感度与调用数              会话窗口放内存（见 ``ai_context``）
========================  ==============================  ====================

文件长这样（``AI_CHAT_DIR/affinity/<清洗名>.<短哈希>.txt``）::

    # Liz 好感度与记忆（自动生成）
    affinity=12
    calls=37
    mem=主玩键盘，常打 143 紫谱||上夜班，作息比较乱
    updated=2026-09-29T20:11:03

⚠️ **裸写一个整数也能读**（``12`` ⇒ 好感度 12）—— 想手改数值时不用记格式。

好感度与**长期记忆**为什么在同一个文件
--------------------------------------
它们业务上是同一件事（「Liz 对你这个人的印象」），而且都要跨会话持久、
都要原子写、都要同一把锁。拆成两个文件只会带来「一个写成功、一个没写成功」
的半吊子状态和额外的锁。记忆以 ``mem=`` 一行、多条用 ``||`` 分隔
（见 :data:`MEM_SEP` / :func:`parse_memories`）。

⚠️ **记忆有硬上限**（:data:`MEM_MAX_ITEMS` 条 / :data:`MEM_MAX_CHARS` 字每条）。
原因不在磁盘，而在**提示词体积** —— 记忆块会被人设一起塞进 system_prompt，
而 ``liz_bot.ai_chat.MAX_PROMPT_CHARS`` 有「人设 + 记忆 + 满窗口必须装得下」
的硬约束（见 ``test_cost_budget``）。超出上限时**丢最旧的**。

为什么落盘、而会话窗口放内存
----------------------------
好感度是**跨会话的长期关系**：容器重建、重新部署都不该把它抹掉。
而对话窗口的寿命只有 30 分钟（``ai_context.DEFAULT_TTL``），落盘没有收益。

数值怎么变
----------
每条被 AI 接管的消息调一次 :func:`delta_for`：

* 默认 **+1**（愿意跟你说话，关系就在慢慢走近）
* 说了好话（谢谢 / 厉害 / 可爱 …）**+3**
* 说了难听话（笨蛋 / 讨厌 / 滚 …）**-3**（难听话优先判定）

范围 **-100 ~ 100**，超出即夹住。数值本身**不回显给用户** ——
除非他主动问（见 :func:`is_inquiry`，那条路不调 API，所以免费且数值准确）。
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from datetime import datetime

from liz_bot.runtime_paths import AFFINITY_DIR

#: 好感度的取值范围。
MIN = -100
MAX = 100

#: 默认增量 / 好话 / 难听话。
DELTA_BASE = 1
DELTA_PRAISE = 3
DELTA_RUDE = -3

#: 判定用的关键词。**刻意短**：群聊里够用就行，长列表只会带来误判。
#:
#: ⚠️ 别加「强」「牛」这类单字 —— 「强调」「牛肉」都会被误判成夸奖。
_PRAISE = ("谢谢", "感谢", "辛苦了", "厉害", "好棒", "太强", "可爱",
           "喜欢", "爱你", "抱抱", "贴贴", "靠谱")
_RUDE = ("笨蛋", "傻子", "傻逼", "滚", "讨厌", "闭嘴", "烦人",
         "垃圾", "废物", "蠢", "有病")

#: 主动询问好感度的触发词。**只要出现「好感」就算问** —— 精确匹配
#: （比如必须整句等于「好感度」）会让「现在好感度多少呀」这种最自然的问法漏掉，
#: 而漏掉的代价是用户以为这个功能不存在。误判的代价只是答非所问一句，可接受。
_INQUIRY = "好感"

#: 文件名里保留的字符；其余一律换成 ``_``。openid 本身就是 ``[A-Za-z0-9_-]``，
#: 所以正常情况不会被替换 —— 这一步是防「键里有奇怪字符」时拼出非法路径。
_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def _path(key: str) -> str:
    """会话键 → 文件路径。

    文件名 = ``<清洗后的键，最多 40 字>.<sha1 前 10 位>.txt``。

    ⚠️ **短哈希不是装饰**：清洗会把 ``:`` 换成 ``_``，于是
    ``群A:成员B`` 与 ``群A_成员B`` 会撞成同一个文件名。哈希取自**原始键**，
    两者必然不同 —— 少了它就等于把两个人的好感度混在一起。
    """
    safe = _SAFE_RE.sub("_", key)[:40]
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return os.path.join(AFFINITY_DIR, f"{safe}.{digest}.txt")


def _ensure_dir() -> None:
    """确保目录存在（``runtime_paths.ensure_dirs`` 启动时也会建，这里兜底）。"""
    try:
        os.makedirs(AFFINITY_DIR, exist_ok=True)
    except OSError:
        # 建不出来就退化成「读不到 = 中性」；不该因为磁盘问题让聊天挂掉
        pass


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

#: 记忆在文件里的键名。**一个键、多条、用 :data:`MEM_SEP` 分隔** ——
#: 刻意不做成「每行一条」：``_parse`` 是逐行 ``partition("=")`` 的，
#: 让 ``mem=`` 占一行、值里再放多条，改动面最小，也不会和别的键混。
_MEM_KEY = "mem"

#: 记忆之间的分隔符。选 ``||`` 是因为它几乎不可能出现在自然语言里，
#: 而单个 ``|`` 太常见。写入时会先把记忆里出现的分隔符清洗掉。
MEM_SEP = "||"

#: 单条记忆的字符上限。超长的一律截断 —— 记忆是「一句提醒」，不是日记。
#: 定 60 的依据：一条有用的记忆（「主玩键盘，常打 143 紫谱」）约 15~25 字，
#: 60 足够容纳稍长的表述，又不至于一条就吃掉半个记忆块。
MEM_MAX_CHARS = 60

#: 最多留几条记忆。**这是硬上限** —— 记忆块要和人设、窗口一起挤
#: :data:`liz_bot.ai_chat.MAX_PROMPT_CHARS`，不封顶会把「贴长文」推进
#: 「丢历史」分支。超出的**丢最旧的**（``memories`` 按时间先后排列）。
MEM_MAX_ITEMS = 8

def _parse(raw: str) -> tuple[int, int]:
    """解析文件内容 → ``(好感度, 调用数)``。**任何异常都退回中性值**。

    容忍三种写法：``key=value`` 多行、裸整数、空文件/垃圾内容。

    ⚠️ **记忆不由本函数解析** —— 它返回的是两个整数，塞不进列表。
    记忆走 :func:`parse_memories`，两边对同一个 ``mem=`` 行的解析口径
    **必须一致**（都按 :data:`MEM_SEP` 切、都清洗空项）。
    """
    value, calls = 0, 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, _, text = line.partition("=")
            name, text = name.strip().lower(), text.strip()
        else:
            # 裸整数 —— 方便手改
            name, text = "affinity", line
        try:
            number = int(text)
        except ValueError:
            continue
        if name == "affinity":
            value = number
        elif name == "calls":
            calls = number
    return max(MIN, min(MAX, value)), max(0, calls)


def parse_memories(raw: str) -> list[str]:
    """从文件内容里取出记忆列表。**任何异常都退回空列表**。

    容错口径与 :func:`_parse` 一致：空行与 ``#`` 注释跳过，
    ``mem=`` 的值按 :data:`MEM_SEP` 切分，空项丢弃。

    ⚠️ 第三条及以后同名的 ``mem=`` 行**会覆盖前面的** —— 与 ``_parse``
    对 ``affinity`` 的处理一致（后写的赢）。正常写入只会有一行，
    这条规则是给「手改坏了」兜底。
    """
    found: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, text = line.partition("=")
        if name.strip().lower() != _MEM_KEY:
            continue
        found = _clean_memories(text.split(MEM_SEP))
    return found


def _clean_memories(items: list[str]) -> list[str]:
    """清洗记忆列表：去空白、丢空项、截断超长、**去重**，并裁到上限。

    去重是必须的：抽取常把同一件事反复说出来（「上夜班」说三次），
    不去重的话 8 条上限会被同一件事吃光。
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = " ".join(str(item or "").split())
        if not text:
            continue
        text = text[:MEM_MAX_CHARS]
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    # 超出上限丢**最旧的**（列表按时间先后排，头部最旧）
    return out[-MEM_MAX_ITEMS:]


def _write(path: str, value: int, calls: int, memories: list[str]) -> None:
    """原子写入（先写临时文件再 ``os.replace``）—— 避免半截文件被读到。

    ⚠️ **记忆必须和好感度写在同一份文件、同一次原子替换里**。
    分成两个文件的话，要么多一把锁、要么出现「好感度写成功、记忆没写成功」
    的半吊子状态 —— 而这两者在业务上本来就是一件事（Liz 对你这个人的印象）。
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("# Liz 好感度与记忆（自动生成）\n")
        fh.write(f"affinity={value}\n")
        fh.write(f"calls={calls}\n")
        if memories:
            # ⚠️ 写入前清掉分隔符 —— 记忆是从模型输出里来的，
            #    万一它写了 ``||``，落盘后再读就会**凭空多出一条**记忆。
            safe = [m.replace(MEM_SEP, " ") for m in memories if m]
            if safe:
                fh.write(f"{_MEM_KEY}={MEM_SEP.join(safe)}\n")
        fh.write(f"updated={datetime.now().isoformat(timespec='seconds')}\n")
    os.replace(tmp, path)


def load(key: str | None) -> int:
    """读好感度；没有记录（或读不动）时为 **0**（中性）。"""
    if not key:
        return 0
    try:
        with open(_path(key), "r", encoding="utf-8") as fh:
            return _parse(fh.read())[0]
    except (OSError, ValueError):
        return 0


def adjust(key: str | None, delta: int) -> int:
    """按 ``delta`` 调整好感度并落盘，返回**调整后**的值。

    ⚠️ 读-改-写必须持锁：同一个人连发两条时，两个协程可能在同一个
    「读到 12」上各加一次，结果只涨了 1。
    ⚠️ **记忆必须一起读出来再一起写回去** —— 否则调一次好感度就把
    记忆抹掉了（``_write`` 是把整个文件重写的）。
    """
    if not key or not delta:
        return load(key)

    path = _path(key)
    with _lock:
        value, calls, memories = _read_all(path)
        value = max(MIN, min(MAX, value + delta))
        calls += 1
        _ensure_dir()
        try:
            _write(path, value, calls, memories)
        except OSError:
            # 写不进去就只影响持久化，本次仍然返回算好的值
            pass
        return value


def _read_all(path: str) -> tuple[int, int, list[str]]:
    """一次读出 ``(好感度, 调用数, 记忆)``。读不动时全用中性值。

    ⚠️ **必须一次读全**：分三次 ``open`` 会在两次读之间被别的协程写掉，
    拿到的三个值不属于同一个快照（读到「新好感度 + 旧记忆」）。
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return 0, 0, []
    value, calls = _parse(raw)
    return value, calls, parse_memories(raw)


def load_memories(key: str | None) -> list[str]:
    """读该会话的长期记忆；没有（或读不动）时为空列表。"""
    if not key:
        return []
    return _read_all(_path(key))[2]


def add_memories(key: str | None, items: list[str]) -> list[str]:
    """把新记忆并入该会话并落盘，返回**合并后**的完整列表。

    合并规则（见 :func:`_clean_memories`）：去空白、截断、去重、超上限丢最旧。

    ⚠️ 与 :func:`adjust` 共用同一把锁与同一次读-改-写：
    抽取记忆与调好感度可能在同一个往返里先后发生，各写一次的话
    后写的那个会把先写的覆盖掉。
    """
    if not key or not items:
        return load_memories(key)

    path = _path(key)
    with _lock:
        value, calls, old = _read_all(path)
        merged = _clean_memories(old + list(items))
        _ensure_dir()
        try:
            _write(path, value, calls, merged)
        except OSError:
            pass
        return merged


def clear_memories(key: str | None) -> None:
    """清空该会话的记忆（**保留好感度**）。给「忘了我吧」这类请求用。"""
    if not key:
        return
    path = _path(key)
    with _lock:
        value, calls, _ = _read_all(path)
        _ensure_dir()
        try:
            _write(path, value, calls, [])
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 语义
# ---------------------------------------------------------------------------

def delta_for(text: str) -> int:
    """这条消息该让好感度变多少。**难听话优先判定**（一句里又骂又夸时算骂）。"""
    body = text or ""
    if any(word in body for word in _RUDE):
        return DELTA_RUDE
    if any(word in body for word in _PRAISE):
        return DELTA_PRAISE
    return DELTA_BASE


def band(value: int) -> int:
    """好感度 → 风格带下标（``0`` 最冷 … ``4`` 最亲近）。

    对应 ``replies.json`` 的 ``ai.style_bands`` / ``ai.affinity_labels``。
    中性带（``2``）刻意**包住 0**，让新用户落在「平常」这一档 ——
    Liz 的原作基线（安静、旁观、温柔、有点疏离）正是这一带。
    """
    if value <= -60:
        return 0
    if value <= -20:
        return 1
    if value < 20:
        return 2
    if value < 60:
        return 3
    return 4


def is_inquiry(text: str) -> bool:
    """这条消息是不是在**主动问**好感度（见 :data:`_INQUIRY`）。"""
    return _INQUIRY in (text or "")


#: 询问「你记得我什么」的触发词。**要求「记得」+ 人称同时出现**才命中 ——
#: 只看「记得」会误伤（「你还记得昨天那个笑话吗」是在聊天，不是在查记忆），
#: 所以用一个正则而不是单个子串。命中后走**本地读取**，不调 API
#: （与好感度同理：让模型转述记忆必然记不全、还会自己编）。
_RECALL_RE = re.compile(
    r"(你|Liz|liz)"
    r"[^。！？\n]{0,6}"
    r"(记得|记住|知道)"
    r"[^。！？\n]{0,6}"
    r"(我|咱)"
)


def is_recall(text: str) -> bool:
    """这条消息是不是在问「你记得我什么」。

    ⚠️ 与 :func:`is_inquiry` 一样**刻意宽松但有边界**：误判的代价只是
    答一句记忆清单，漏判的代价是用户以为这功能不存在 —— 前者更可接受。
    但仍加了人称约束，否则「我记得说过…」这种自述也会被当成查询。
    """
    return bool(_RECALL_RE.search(text or ""))
