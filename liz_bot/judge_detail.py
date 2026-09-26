"""谱面判定细节 —— 物量分布 + 每个判定档位的扣分。

替代原先 ``song_query.songdata_reply`` 的「原样 dump 内部结构」（2026-09-25）。

数据从哪来
==========

**物量**（各音符类型的颗数）来自曲库 ``charts[i].notes`` —— diving-fish 格式。
⚠️ **数组长度决定含义，不能按固定下标读**：touch 只有 DX 谱才有，而且它是
**从中间省略**的，不是末尾 ——

    5 位（DX）: [tap, hold, slide, touch, break]
    4 位（SD）: [tap, hold, slide, break]        ← touch 省略后 break 前移到位 3

所以 SD 谱的位 3 是 **break**，DX 谱的位 3 是 **touch**。依据是 diving-fish
官方 API 文档对 ``/music_data`` 的 ``charts[].notes`` 的描述：
「依次为 Tap、Hold、Slide、（Touch，仅 DX 类型）、Break」。

⚠️ **2026-09-26 修正**：本模块原先按固定的 ``[tap, hold, slide, break, touch]``
读，于是**所有 DX 谱的 touch 与 break 被互换**（3285 张），理论分与 break 明细
全错。SD 谱恰好不受影响（4 位里位 3 本来就是 break），所以当初用 SD 曲
``id 143`` 反算达成率才会「歪打正着」地吻合 —— 那条验证**无法覆盖 DX**。
现在按长度分派，见 :data:`NOTES_ORDER_BY_LEN`。

**分值**不是猜的，来自**网络上流传的** maimai DX 客户端反编译产物 ——
其中的判定得分表 ``judgeScoreTbl``（每种音符类型 × 每个判定档位的得分）、
判定档位枚举，以及理论分的累加函数。

本项目**只取其中的数值常量**（就是下面 :data:`BASE_SCORE` 那 5 个数和
:data:`BREAK_TIERS` 的档位划分），**不包含、也不分发该反编译产物的任何代码或
文件** —— 仓库里没有任何代码依赖它。

数值正确性已用真实成绩交叉验证：按下面这套常量复算 ``id 143`` Re:Master 的
达成率得 **99.598910%**，与机台实测精确吻合（差 < 1e-5）。
该断言固化在 ``_tools/test_command_table.py`` 的 ⑨ 段里 —— 改这里的常量会让
它立刻失败，这正是我们要的。

达成率公式::

    达成率 = 常规分 / 常规理论分 × 100 + BreakBonus分 / BreakBonus理论分 × 1

第二项最多 +1%，这就是全 CP 能到 **101%** 的由来。

三条关键结论（决定了本模块的表长什么样）
========================================

1. **Perfect 与 Critical Perfect 在达成率上同分**（同类音符得分完全相同），
   于是**普通音符的扣分只由 Great / Good / Miss 决定**，cp/p 的扣分恒为 0。
   所以主表**只列 great / good / miss 三行**，不列 cp/p（一行 0.0000 没有信息量，
   用户 2026-09-25 决定删掉）；表下那句解释也在 2026-09-26 一并删了
   （用户要求 —— 表里本来就没有，再解释一句纯属噪音）。两者差异只体现在
   DX 分数（3 vs 2）与 BreakBonus 上。
2. 普通音符的扣分与**音符权重**成正比：``tap = touch = 1``、``hold = 2``、
   ``slide = 3``、``break = 5``（倍率相对 tap）。
   所以 hold 的扣分恰好是 tap 的 2 倍、slide 是 3 倍 —— 表里能直接看出来。
3. **BREAK 是唯一 CP 与 P 不同分的类型**（BreakBonus 100 vs 75），
   而且它的 Great 还分 3 档（音符得分 2000 / 1500 / 1250）。
   ⇒ 它没法塞进主表的三行里，单独一张表。

排版
====

主表按「列 = 音符类型、行 = 判定」排（用户指定）::

            tap    hold   slide   touch  break
    数量     441      19     192       -      3
    great 0.0187  0.0374  0.0561       -      -
    good  0.0467  0.0935  0.1402       -      -
    miss  0.0935  0.1869  0.2804       -      -

BREAK 明细表**横排、4 档一行**（用户 2026-09-25 选定）——
8 档竖排要 10 行，横排只要 6 行；一行放 8 档则有 60+ 格必然折行。
每档占一列、占三行（档位名 / 得分+加成 / 扣分）::

    BREAK 3 颗
    （每颗：得分+加成 → 扣分）
    100p     75p      50p     great1
    2500+100 2500+ 75 2500+ 50 2000+ 40
    0.0000   0.0833   0.1667  0.2935
    great2   great3   good    miss
    1500+ 40 1250+ 40 1000+ 30    0+  0
    0.3869   0.4336   0.5137  0.8006

「得分+加成」那一行是必要的，不是装饰：100p / 75p / 50p 的**音符得分完全相同**
（都是 2500），差别只在加成（100 / 75 / 50）；great1/2/3 则相反 —— 加成都是 40、
得分是 2000 / 1500 / 1250。不写出这一行，用户看不出这些档位为什么扣分不同。

对齐用的是**显示宽度**（CJK 算 2 格）而不是 ``len()`` —— 否则 ``数量``（2 字符
4 格）会和 ``great``（5 字符 5 格）错开。实现在 :mod:`liz_bot.text_layout`
（候选列表也共用同一套口径）。前提是客户端把 CJK 渲染成 2 倍宽，
手机端用比例字体时仍可能歪，这一点无法在服务端解决。
"""

from __future__ import annotations

import logging

from liz_bot import replies, song_query, text_layout

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量 —— 全部来自游戏源码，见模块文档
# ---------------------------------------------------------------------------

#: 主表的列顺序。**与曲库 ``notes`` 数组的顺序不同** —— 这里把 break 放最后，
#: 因为它的扣分在主表里不展开（有自己的明细表）。
COLUMN_ORDER = ("tap", "hold", "slide", "touch", "break")

#: 曲库 ``charts[].notes`` 的**规范顺序**（DX 谱，5 位）。用作「全部音符类型」的
#: 枚举 —— 理论分求和与补零都走它，所以 :func:`_counts` 必须把 5 个键都返回。
#: ⚠️ 别拿它直接索引 ``notes``，SD 谱是 4 位（见 :data:`NOTES_ORDER_BY_LEN`）。
NOTES_ORDER = ("tap", "hold", "slide", "touch", "break")

#: ``notes`` 的**实际**数组顺序 —— **按长度分派**。
#:
#: diving-fish 只在 DX 谱里给 touch，SD 谱**从中间省略**它，于是 break 前移：
#:
#: * 5 位（DX）``[tap, hold, slide, touch, break]``
#: * 4 位（SD）``[tap, hold, slide, break]``
#:
#: 所以「位 3」在两种谱面里含义不同 —— 这是本模块最容易踩的坑。
NOTES_ORDER_BY_LEN: dict[int, tuple[str, ...]] = {
    5: ("tap", "hold", "slide", "touch", "break"),
    4: ("tap", "hold", "slide", "break"),
}

#: 每个音符的满分（= Critical 档得分），``judgeScoreTbl`` 第 7 列。
#: 权重比 tap:hold:slide:break = 1:2:3:5（touch 同 tap）。
BASE_SCORE = {"tap": 500, "hold": 1000, "slide": 1500, "touch": 500, "break": 2500}

#: 单颗 BREAK 的加成满分。整谱 break 加成合计 = break 颗数 × 100，
#: 而它在达成率里恒占 1%，所以「一颗 break 的加成 = 1 / break颗数 %」。
BREAK_BONUS_MAX = 100

#: 主表展示的判定行（按扣分从好到差）。
#:
#: ⚠️ **刻意不含 cp/p** —— 普通音符的 Critical Perfect 与 Perfect 得分完全相同，
#: 扣分**恒为 0**，留着就是一行没信息量的 ``0.0000``（用户 2026-09-25 决定删掉）。
#: 表下也**不再解释**这件事（用户 2026-09-26 要求），所以 ``judge.unit`` 里没有它。
#:
#: 得分率 0.8 / 0.5 / 0.0 来自 ``judgeScoreTbl``，对 tap/hold/slide/touch 通用。
NORMAL_RATE = (("great", 0.8), ("good", 0.5), ("miss", 0.0))

#: BREAK 明细表每行放几档。8 档拆两行（4+4）—— 横排比竖排省 4 行，
#: 而一行放满 8 档会有 60+ 格、在手机 QQ 上必然折行（用户 2026-09-25 选定）。
BREAK_PER_ROW = 4

#: BREAK 的档位 → ``(音符得分率, 加成率)``，8 档全部来自 ``judgeScoreTbl``。
#:
#: ⚠️ 档位名与右边的数值**按下标绑定**，所以刻意写在同一处 ——
#: 拆到 ``replies.json`` 里会让「改标签时忘了改数值」成为可能。
#: 100p / 75p / 50p 对应加成 100 / 75 / 50（即 Perfect 家族的三档）。
BREAK_TIERS = (
    ("100p", 1.0, 1.00),
    ("75p", 1.0, 0.75),
    ("50p", 1.0, 0.50),
    ("great1", 0.8, 0.40),
    ("great2", 0.6, 0.40),
    ("great3", 0.5, 0.40),
    ("good", 0.4, 0.30),
    ("miss", 0.0, 0.00),
)

#: 难度写法 → 下标。数字是 **0 基**（与 maimai 协议、``_tools/live_upload.py``、
#: ``song_query.IDX_*`` 一致），另外收社区通用的颜色叫法。
#:
#: ⚠️ 刻意**不**收 ``1``-``5``：0 基与 1 基会撞车（``1`` 到底是 Basic 还是
#: Advanced？），猜错就是给用户看错谱面。宁可报错并列出可填值。
DIFFICULTY_ALIASES = {
    "0": 0, "basic": 0, "bas": 0, "b": 0, "绿": 0, "绿谱": 0,
    "1": 1, "advanced": 1, "adv": 1, "a": 1, "黄": 1, "黄谱": 1,
    "2": 2, "expert": 2, "exp": 2, "e": 2, "红": 2, "红谱": 2,
    "3": 3, "master": 3, "mas": 3, "m": 3, "紫": 3, "紫谱": 3,
    "4": 4, "remaster": 4, "remas": 4, "re": 4, "白": 4, "白谱": 4, "里": 4,
}

#: **名字型**难度写法（``绿`` / ``master`` …）—— 即 :data:`DIFFICULTY_ALIASES`
#: 里**去掉纯数字** ``0``-``4`` 之后剩下的那些。
#:
#: 用途只有一个：``/songdata`` / ``/估分`` 的**乐曲混合检索**要找「难度在哪一格」
#: 当锚点（歌名可能含空格，不能按位置硬切，见
#: :func:`liz_bot.command_router._split_song_query`）。
#:
#: ⚠️ **为什么锚点必须是名字型**：数字 ``0``-``4`` 与百分比、DX 星级、combo
#: 等级**撞车** —— ``/估分 147 2 100.0`` 里的 ``2`` 是难度还是星级？
#: 名字型（绿黄红紫白 / basic adv exp mas remas）不可能出现在别的参数位，
#: 拿它当锚点**无歧义**。数字难度照旧可用，只是不能兼任锚点。
NAMED_DIFFICULTIES = frozenset(
    name for name in DIFFICULTY_ALIASES if not name.isdigit()
)


# ---------------------------------------------------------------------------
# 解析与计算
# ---------------------------------------------------------------------------

def difficulty_names() -> list[str]:
    """难度显示名列表（``["Basic", "Advanced", ..., "Re:Master"]``），下标即难度。"""
    return list(replies.get("judge.difficulties"))


def resolve_difficulty(text) -> int | None:
    """把用户写的难度解析成 0-4 下标；不认识返回 ``None``。

    大小写不敏感（``Master`` / ``MASTER`` / ``master`` 等价）；中文不受影响。
    """
    if not isinstance(text, str):
        return None
    return DIFFICULTY_ALIASES.get(text.strip().lower())


def _counts(chart: dict) -> dict[str, int]:
    """把 ``charts[i].notes`` 摊成 ``{音符类型: 颗数}``（5 个键齐全）。

    **按数组长度分派顺序** —— 见 :data:`NOTES_ORDER_BY_LEN`。
    这不是防御性编程，是格式本身如此：SD 谱 4 位 ``[tap, hold, slide, break]``、
    DX 谱 5 位 ``[tap, hold, slide, touch, break]``，位 3 的含义随长度变化。

    长度不在表里时（格式变了 / 脏数据）退回 :data:`NOTES_ORDER` 并按需补零，
    至少不会 ``IndexError``。缺的类型一律按 0 计 —— 不是错误，SD 谱本来就没有
    touch。
    """
    raw = chart.get("notes") or []
    order = NOTES_ORDER_BY_LEN.get(len(raw), NOTES_ORDER)
    counts: dict[str, int] = {note_type: 0 for note_type in NOTES_ORDER}
    for index, note_type in enumerate(order):
        value = raw[index] if index < len(raw) else 0
        counts[note_type] = value if isinstance(value, int) and value > 0 else 0
    return counts


def _theory_scores(counts: dict[str, int]) -> tuple[int, int]:
    """``(常规理论分, break 加成理论分)``。

    前者是全 CP 时所有音符的得分合计；后者是 break 加成合计（恒对应 1%）。
    两者都可能为 0（空谱面），调用方负责避免除零。
    """
    base = sum(counts[t] * BASE_SCORE[t] for t in NOTES_ORDER)
    return base, counts["break"] * BREAK_BONUS_MAX


def _normal_loss(note_type: str, rate: float, base: int) -> float:
    """普通音符在得分率 ``rate`` 下的扣分（百分点，**相对满分**）。"""
    if not base:
        return 0.0
    return BASE_SCORE[note_type] * (1.0 - rate) / base * 100.0


def _break_loss(score_rate: float, bonus_rate: float, base: int, bonus: int) -> float:
    """BREAK 音符的扣分（百分点）= 音符得分亏的部分 + 加成亏的部分。

    加成部分单独归一化：整谱 break 加成恒占 1%，所以这里乘 1 而不是 100。
    """
    note_part = BASE_SCORE["break"] * (1.0 - score_rate) / base * 100.0 if base else 0.0
    bonus_part = BREAK_BONUS_MAX * (1.0 - bonus_rate) / bonus if bonus else 0.0
    return note_part + bonus_part


# ---------------------------------------------------------------------------
# 排版
# ---------------------------------------------------------------------------

# 显示宽度对齐的实现已抽到 :mod:`liz_bot.text_layout`（候选列表也要用同一套口径）。
# 这里保留 ``_width`` / ``_pad`` 两个**模块级别名**：测试与既有调用方按这两个名字用。
_width = text_layout.display_width
_pad = text_layout.pad


def _cell(value: float) -> str:
    """扣分单元格：固定 4 位小数（与游戏显示精度一致）。

    实测上限 7 字符（``5.0000``）—— 最小谱面的 tap miss 也就 5% 出头，
    所以定宽 6 足够，不会撑破列。
    """
    return f"{value:.4f}"


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def judge_detail_reply(song_id: str, difficulty: str) -> str:
    """``/songdata <歌曲id> <难度>`` 的回复 —— 该谱面各判定的扣分明细。

    :param song_id: 曲目 ID（只走 ``BY_ID``）
    :param difficulty: 难度写法，见 :data:`DIFFICULTY_ALIASES`
    :return: 渲染好的文本；难度不认识 / 该难度不存在 / 曲目不存在 /
        曲库载入失败 各有对应文案
    """
    names = difficulty_names()

    index = resolve_difficulty(difficulty)
    if index is None:
        return replies.text("judge.bad_difficulty", value=difficulty)

    try:
        hits = song_query.select_song(
            select_data=song_id, select_type=song_query.BY_ID)
    except (OSError, ValueError):
        # 与 songdata 旧实现同样的判据：文件缺失/读不动是 OSError，
        # 结构损坏是 ValueError（JSONDecodeError 是其子类）。
        # 其余异常照旧上抛 —— 那是程序缺陷，不该伪装成「DataError」。
        logger.exception("判定细节：曲库载入失败 song_id=%r", song_id)
        return replies.text("song.data_error")

    if not hits:
        return replies.text("song.not_found")

    song = hits[0]["song"]
    charts = song.get("charts") or []
    if index >= len(charts):
        # 宴会场曲目只有 1 张谱面、SD 曲目没有 Re:Master —— 这不是错误输入，
        # 所以把「它有哪些难度」一并回给用户，省一次查询。
        #
        # ⚠️ **一行一个难度**：拼成一行时实测最宽 79 格
        #    （「…只有：Basic 5 / Advanced 7+ / Expert 10+ / Master 12+」），
        #    手机上必然折 2-3 行；拆行后每行 ≤ 15 格。
        levels = song.get("level") or []
        available = "\n".join(
            replies.text("judge.available_line", name=names[i], level=levels[i])
            for i in range(len(charts))
            if i < len(levels) and i < len(names)
        )
        return replies.text(
            "judge.no_chart",
            difficulty=names[index] if index < len(names) else str(index),
            available=available,
        )

    counts = _counts(charts[index])
    base, bonus = _theory_scores(counts)
    level = (song.get("level") or [None] * len(charts))[index]
    charter = (charts[index].get("charter") if isinstance(charts[index], dict) else None)
    missing = replies.text("judge.placeholder")

    header = replies.text(
        "judge.header",
        title=song.get("title"),
        difficulty=names[index] if index < len(names) else str(index),
        level=level if level is not None else missing,
        charter=charter if charter else missing,
    )
    scale = replies.text(
        "judge.scale", base_score=base, bonus_score=bonus)

    # ---- 主表：列 = 音符类型，行 = 判定 ----
    labels = [
        replies.text("judge.row_count"),
        *(replies.text(f"judge.row_{key}") for key, _ in NORMAL_RATE),
    ]

    # break 在主表里不展开（它 8 档，见下），该谱面没有的音符类型也记 '-'
    def main_cell(note_type: str, text: str) -> str:
        if note_type == "break" or not counts[note_type]:
            return missing
        return text

    count_cells = [
        missing if not counts[t] else str(counts[t]) for t in COLUMN_ORDER]
    judge_rows = []
    for _key, rate in NORMAL_RATE:
        judge_rows.append([
            main_cell(t, _cell(_normal_loss(t, rate, base))) for t in COLUMN_ORDER
        ])

    widths = [
        max(_width(COLUMN_ORDER[i]),
            *(_width(row[i]) for row in [count_cells, *judge_rows]))
        for i in range(len(COLUMN_ORDER))
    ]
    label_width = max(_width(x) for x in labels)

    def line(label: str, cells: list[str]) -> str:
        body = " ".join(_pad(c, w) for c, w in zip(cells, widths))
        return _pad(label, label_width) + " " + body

    table = [
        " " * label_width + " " + " ".join(
            _pad(COLUMN_ORDER[i], widths[i]) for i in range(len(COLUMN_ORDER))),
        line(labels[0], count_cells),
        *(line(labels[i + 1], judge_rows[i]) for i in range(len(judge_rows))),
    ]

    parts = [header, scale, "\n".join(table), replies.text("judge.unit")]

    # ---- BREAK 明细（横排，每行 BREAK_PER_ROW 档；每档占一列、占三行）----
    if counts["break"]:
        cells = []
        for tier, score_rate, bonus_rate in BREAK_TIERS:
            # ⚠️ 变量名不能叫 bonus —— 会和 _break_loss 的分母参数（整谱加成理论分）
            #    撞名，静默算出一堆看似合理实则全错的数（实测踩过）。
            tier_score = int(BASE_SCORE["break"] * score_rate)
            tier_bonus = int(BREAK_BONUS_MAX * bonus_rate)
            cells.append((
                tier,
                # 得分定宽 4 位（2500/2000/1500/1250/1000/0）、加成定宽 3 位
                # （100/75/50/40/30/0）—— 定宽后「+」才会在列内竖着对齐
                f"{tier_score:>4}+{tier_bonus:>3}",
                _cell(_break_loss(score_rate, bonus_rate, base, bonus)),
            ))

        parts.append(replies.text("judge.break_title", count=counts["break"]))
        parts.append(replies.text("judge.break_hint"))
        for start in range(0, len(cells), BREAK_PER_ROW):
            block = cells[start:start + BREAK_PER_ROW]
            # 每列宽度 = 该列三行（档位名 / 得分+加成 / 扣分）里最宽的那个。
            # 「得分+加成」是 8 格，所以列宽总是 8 —— 档位名与扣分随之右对齐。
            #
            # ⚠️ 别把这里的下标搞混：**外层是列、内层是字段**。
            #    widths 的长度 = 本行的列数（≤ BREAK_PER_ROW），
            #    不是字段数（len(cells[0])）—— 反了就会 widths[3] 越界（踩过）。
            widths = [max(_width(field) for field in cell) for cell in block]
            for i in range(len(cells[0])):
                parts.append(" ".join(
                    _pad(cell[i], widths[j]) for j, cell in enumerate(block)))
    else:
        parts.append(replies.text("judge.no_break"))

    return "\n".join(parts)
