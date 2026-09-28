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

    # Liz 好感度（自动生成）
    affinity=12
    calls=37

⚠️ **裸写一个整数也能读**（``12`` ⇒ 好感度 12）—— 想手改数值时不用记格式。

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

def _parse(raw: str) -> tuple[int, int]:
    """解析文件内容 → ``(好感度, 调用数)``。**任何异常都退回中性值**。

    容忍三种写法：``key=value`` 多行、裸整数、空文件/垃圾内容。
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


def _write(path: str, value: int, calls: int) -> None:
    """原子写入（先写临时文件再 ``os.replace``）—— 避免半截文件被读到。"""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("# Liz 好感度（自动生成）\n")
        fh.write(f"affinity={value}\n")
        fh.write(f"calls={calls}\n")
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
    """
    if not key or not delta:
        return load(key)

    path = _path(key)
    with _lock:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                value, calls = _parse(fh.read())
        except OSError:
            value, calls = 0, 0

        value = max(MIN, min(MAX, value + delta))
        calls += 1
        _ensure_dir()
        try:
            _write(path, value, calls)
        except OSError:
            # 写不进去就只影响持久化，本次仍然返回算好的值
            pass
        return value


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
