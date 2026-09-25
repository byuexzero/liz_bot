"""文本对齐 —— 按**显示宽度**而不是字符数计算。

为什么需要单独一个模块
======================

QQ 的消息正文用比例字体渲染，CJK 字符占的横向空间约等于两个半角字符。
于是 ``len("数量") == 2`` 但它占 **4 格**，直接用 ``len()`` 补齐会让
``数量`` 与 ``great`` 错开。

这条规则现在有两处要用，所以收在一处：

    judge_detail.py   判定明细表的列对齐
    song_query.py     候选列表里的长曲名截断（全库最长曲名 76 格）

⚠️ **前提是客户端把 CJK 渲染成 2 倍宽。** 手机端若用比例字体，对齐仍可能歪，
这一点无法在服务端解决 —— 本模块只保证「按等宽字体的假设算对了」。

宽度口径
========

只有 ``east_asian_width`` 为 ``W``（Wide）/ ``F``（Fullwidth）的字符算 2 格，
其余（含 ``A`` / Ambiguous，如 ``→`` ``·`` ``～``）算 1 格。
这与本项目的既有实测口径一致（``judge_detail`` 的表格宽度守卫就按这个算）。
"""

from __future__ import annotations

import unicodedata

#: 省略号。中文排版习惯用一个字符，占 1 格。
ELLIPSIS = "…"


def char_width(char: str) -> int:
    """单个字符的显示宽度：``W`` / ``F`` 类算 2 格，其余算 1 格。"""
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def display_width(text: str) -> int:
    """字符串的显示宽度（格数）。"""
    return sum(char_width(c) for c in text)


def pad(text: str, width: int, right: bool = True) -> str:
    """按**显示宽度**补空格到 ``width`` 格。

    :param right: ``True`` 右对齐（数字列用），``False`` 左对齐
    :return: 补好空格的字符串；``text`` 本来就超宽时原样返回（不截断）
    """
    spaces = " " * max(0, width - display_width(text))
    return spaces + text if right else text + spaces


def truncate(text: str, limit: int, ellipsis: str = ELLIPSIS) -> str:
    """按**显示宽度**截断到 ``limit`` 格，超出时以 ``ellipsis`` 收尾。

    省略号**算在 ``limit`` 之内**（即结果的总宽度 ≤ ``limit``），
    所以「截断后的行」与「没截断的行」宽度口径一致，可以混排。

    截断点按**字符**取整 —— 不会把一个宽字符切成半格（那会得到一堆乱码般的
    半宽字符）。代价是实际宽度可能比 ``limit`` 少 1 格，对齐仍成立。

    ::

        truncate("チルノのパーフェクトさんすう教室", 20)   # → 'チルノのパーフェクトさんすう…'
        truncate("abc", 20)                                # → 'abc'（没超，原样返回）
    """
    if display_width(text) <= limit:
        return text

    budget = limit - display_width(ellipsis)
    if budget <= 0:
        return ellipsis

    kept: list[str] = []
    used = 0
    for char in text:
        size = char_width(char)
        if used + size > budget:
            break
        kept.append(char)
        used += size
    return "".join(kept) + ellipsis
