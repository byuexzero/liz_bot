"""估分 —— 给定「目标达成率 + DX 星级」，反推出**可上传的判定分布**。

与 :mod:`liz_bot.judge_detail` 的关系
=====================================

``judge_detail`` 是**正向**的：谱面物量 → 各判定档位的扣分表。
本模块是**反向**的：目标达成率 → 判定分布。两者共用同一套常量与公式
（:data:`SCORE` / :data:`BONUS` 直接由 ``judge_detail`` 的分值表推出），
所以「正向算出来的达成率」与「反向给的目标」必然在同一把尺子上。

达成率公式（与游戏一致，见 ``GameScoreList.ProcessCalcJudgeBreak``）::

    达成率 = 常规分 / 常规理论分 × 100 + BreakBonus分 / BreakBonus理论分 × 1

第二项最多 +1%，所以全 CP 是 **101%**。本模块内部一律用整数运算
（达成率单位 1/10000 %），**不用浮点** —— 浮点会让「≥ 目标」这种边界判断
在最后一位上抖，而这一位正好是用户能看见的。

问题形式
========

记 ``D = 101 - 达成率``（百分点）。目标是：

* ``达成率 ≥ 目标`` ⟺ ``D ≤ Dmax``；
* DX 分数落在指定星级的区间内 ⟺ ``deficit ≤ hi``（deficit = DX 满分 - DX 分）。

于是要解的是：**在「D ≤ Dmax」且「deficit ≤ hi」下把 D 取到最大**
（D 最大 = 达成率最小 = 向上估计取到最小可达值）。

每颗音符从 CriticalPerfect 往下降一档，就得到一件「降级项」
``(scoreLoss, bonusLoss, dxLoss, 数量上限)``。所以这是一个
**带值上限的有界背包**。解法见 :func:`solve`。

精度口径（**重要**）
====================

产品口径是「达成率与目标差 **0.1 个百分点**以内即可」，不追求数学上的
「最小可达」。这一点被用足了：搜索一旦找到落进 ``[目标, 目标 + 0.1%]``
的解就**立刻收工**（:data:`TOLERANCE`），不必把背包填满。实测把绝大多数
输入的耗时从秒级压到毫秒级。

代价是极少数输入的结果会比理论最小值高最多 0.1%。结果里带了
``Solution.gap`` / ``Solution.within_tolerance``，出图时可以如实标注。
**gap 超过 0.1% 时一定要告警**（见 :func:`estimate_reply`）—— 那说明目标值
本身不可达，给的是「最接近的可行值」，不是用户要的那个数。

连击等级（combo）
==================

``combo`` 参数把「允许用哪些判定档位」锁住，于是生成出来的记录会显示
指定的连击状态（FC / FC+ / AP / AP+，见 :data:`COMBO_FORBID` /
:data:`COMBO_NEED`）。它是**「恰好等于」而不是「不低于」** —— 想要 FC
就必须留一颗 Good，否则会被判成 FC+。

⚠️ 锁得越死，可达的达成率越稀疏：

* ``AP`` 只允许 CP/P ⇒ **普通音符一颗都不扣分**，达成率只能是
  ``101% - 25k / 加成理论分`` 这一串离散值（k = 降成 P 的 break 数）。
  以 147 为例，最接近 100.0% 的可行值是 **100.75%**，差 0.75%。
* ``AP+`` 只允许全 CP ⇒ 达成率只能是 **101%**，且必然是 5★。

这种情况不是求解失败，而是数学上就取不到。所以返回值照给，但
``gap`` 会超过容差，由 :func:`estimate_reply` 显式告警。

DX 星级
=======

阈值来自反编译件 ``DB/DeluxcorerankrateIDEnum.cs``（``Achieve`` 字段），
分母是 **音符总数 × 3**（DX 满分）：::

    1★ 85%   2★ 90%   3★ 93%   4★ 95%   5★ 97%

判定用游戏原式 ``border[j] = achieve[j] × 满分 / 100 + 1``，
星级 = 满足 ``dx ≥ border[j]`` 的最大 j。

⚠️ 所以 **5★ 不需要全 CP**（97% 即可），而**全 Perfect 只有 0★**
（P 每颗 2 分、满分 3 分 ⇒ 66.7%）。这正是「同是 100% 达成率，
CP 与 P 的比例决定星级」的原因 —— CP→P 不改变达成率，是免费的调节旋钮。

BREAK 的档位约定
================

playlog 只有 ``breakGreat`` 一个字段，但游戏里 Great 分 3 档
（得分 2000/1500/1250，加成都是 40）。本模块**固定按 great1（得分率 0.8）
计**，即 ``judge_detail.BREAK_TIERS`` 的第一档 —— 与 ``judge_detail``
展示的扣分表同口径。真实成绩若落在 great2/great3，达成率会有微小出入。

缓存
====

生成结果会按会话键存进 :data:`CACHE`，**保留一轮对话**（只存不处理，
接口先留着）。见 :class:`EstimateCache`。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from liz_bot import judge_detail as jd, replies, song_query, text_layout

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 判定档位（从好到差）。与 ``maimai.user_data`` 的 ``_NOTE_RANKS`` 一致。
RANKS: tuple[str, ...] = ("CriticalPerfect", "Perfect", "Great", "Good", "Miss")

#: 音符类型。与 ``judge_detail.NOTES_ORDER`` 一致（**规范顺序**，不是曲库数组顺序）。
ZONES: tuple[str, ...] = ("tap", "hold", "slide", "touch", "break")

#: 除 break 外的音符类型（它们的 CP 与 P 得分相同，且没有加成）。
NORMAL_ZONES: tuple[str, ...] = ("tap", "hold", "slide", "touch")

#: 可以「CP → P 免费补位」的音符类型（见 :func:`_pad_to_band`）。
#:
#: ⚠️ **比 :data:`NORMAL_ZONES` 少一个 slide**，这是真源推出来的，不是猜的。
#: ``NoteJudge.judgeParamTbl[2]``（``EJudgeType.SlideOut``）是::
#:
#:     -36, -26, -22, -18, -14, -14, -14, 0, 14, 14, 14, 16, 22, 26, 36
#:      [0]  [1]  [2]  [3]  [4]  [5]  [6]  [7] [8] [9][10] [11] ...
#:
#: 而 ``GetJudgeTiming`` 的判法是「``< [4]`` → FastGreat、``< [5]`` →
#: FastPerfect2nd、``< [6]`` → FastPerfect、``<= [8]`` → Critical」。
#: 这里 ``[4] = [5] = [6] = -14``、``[8] = [9] = [10] = 14``，于是
#: **FastPerfect / FastPerfect2nd / LatePerfect / LatePerfect2nd 四段全是空区间**，
#: ``-14 .. +14`` 整段都是 Critical。
#:
#: 结论：**slide 只可能 CP / Great / Good / Miss，没有小 P。**
#: 把它算进补位容量会生成一份游戏里打不出来的记录。
#: （touch 相反：``judgeParamTbl[3]`` 的早侧 ``[0..6]`` 全是 ``-9``，
#: Fast* 段全空 ⇒ **touch 没有 Fast 判定**；晚侧 ``[9] = 10.5`` 是 LatePerfect，
#: 所以 touch **有**小 P，可以补位。）
PAD_ZONES: tuple[str, ...] = ("tap", "hold", "touch")

#: 普通音符得分率（Great 0.8 / Good 0.5 / Miss 0），来自 ``judgeScoreTbl``。
_NORMAL_RATE = (1.0, 1.0, 0.8, 0.5, 0.0)
#: BREAK 得分率 —— ⚠️ Good 是 **0.4**，不是普通音符的 0.5。见 ``judge_detail.BREAK_TIERS``。
_BREAK_RATE = (1.0, 1.0, 0.8, 0.4, 0.0)
#: BREAK 加成（满分 100）：CP 100 / P 75 / Great 40 / Good 30 / Miss 0。
_BONUS = (100, 75, 40, 30, 0)
#: DX 分：CP 3 / P 2 / Great 1 / 其余 0。
_DXPT = (3, 2, 1, 0, 0)

#: 音符类型 → 5 个判定档位的**音符得分**（整数）。由 ``judge_detail`` 的
#: 分值表推出，改那边的常量这里会跟着变（``_selftest`` 会校验）。
SCORE: dict[str, tuple[int, ...]] = {
    z: tuple(int(round(jd.BASE_SCORE[z] * r)) for r in _NORMAL_RATE)
    for z in NORMAL_ZONES
}
SCORE["break"] = tuple(int(round(jd.BASE_SCORE["break"] * r)) for r in _BREAK_RATE)

#: 判定档位 → BREAK 加成。
BONUS: dict[str, int] = dict(zip(RANKS, _BONUS))
#: 判定档位 → DX 分。
DXPT: dict[str, int] = dict(zip(RANKS, _DXPT))

#: 星级阈值（占 DX 满分的百分比）。下标即星级。
STAR_ACHIEVE: tuple[int, ...] = (0, 85, 90, 93, 95, 97)
#: 最高星级。
MAX_STAR = len(STAR_ACHIEVE) - 1

#: 达成率上限（101%），单位 1/10000 %。
ACH_MAX = 1010000
#: 达成率下限（0%）。
ACH_MIN = 0

#: 「够用」的容差：达成率与目标相差不超过这个数就算达标，单位 1/10000 %。
#: ``1000`` = 0.1 个百分点。**这是产品口径，不是近似** —— 搜索一旦找到落进
#: ``[目标, 目标 + 容差]`` 的解就算成功，不再去把背包填满求「最小可达」。
TOLERANCE = 1000

#: 优先争取的更紧容差。命中它就**立刻收工**，省掉 :data:`GRACE` 那段额外搜索。
#: ``200`` = 0.02%，已经小到在四位小数的展示上基本看不出来。
PREFER_TOLERANCE = 200

#: 已经达标但还不够紧时，最多再多搜这么久（秒）就去用现有结果。
#: 这是纯粹的「顺手改善一下」预算，不影响正确性。
GRACE = 0.4

#: 组合搜索的状态数上限。超过就**按当前已探索到的状态收尾**（结果仍合法，
#: 只是可能没落进容差带）。见 :func:`solve` 的 ``exact`` 返回值。
#: 200k 是实测出来的拐点：极端输入（低目标 + 低星级，可达域最大）在 120k
#: 时会截断，150k 起就够；留到 200k 换余量，峰值内存约 90MB。
MAX_STATES = 200_000

#: 组合搜索的时间上限（秒）。与 :data:`MAX_STATES` 双保险 ——
#: 状态数没超但单步很慢时（超大谱面）也要能收住。
TIME_BUDGET = 3.0


# ---------------------------------------------------------------------------
# 连击等级（combo 等级）
# ---------------------------------------------------------------------------
#
# 等级编号与 ``maimai.user_data.PlayComboFlagID`` 一致：0 无 / 1 FC / 2 FC+ /
# 3 AP / 4 AP+。
#
# ⚠️ 每一档**不是「不低于」而是「恰好等于」**。判定真源是
# ``GameScoreList.cs`` 的 ``ComboType``（约 1311 行）::
#
#     if (MissNum == 0 && TheoryCombo != 0)
#     {
#         if (TheoryCombo == CriticalNum)                          → AllPerfectPlus
#         else if (TheoryCombo == CriticalNum + PerfectNum)        → AllPerfect
#         else if (TheoryCombo == ... + GreatNum)                  → Gold   (FC+)
#         else if (TheoryCombo == ... + GoodNum)                   → Silver (FC)
#     }
#
# 也就是说：**FC 必须有 Good、FC+ 必须有 Great、AP 必须有 Perfect**，
# 否则会被判成更高一档。所以「想要 FC」= 禁掉 Miss **且**至少留一颗 Good。
#
# 只有 ``combo == COMBO_ANY`` 是「不限」—— 那时算法自由发挥（结果可能是任意一档）。

#: 未指定 combo 等级。
COMBO_ANY = -1
#: 合法等级。
COMBO_LEVELS = (0, 1, 2, 3, 4)
#: 等级 → 禁用的判定档位。
COMBO_FORBID: dict[int, tuple[str, ...]] = {
    0: (),
    1: ("Miss",),
    2: ("Miss", "Good"),
    3: ("Miss", "Good", "Great"),
    4: ("Miss", "Good", "Great", "Perfect"),
}
#: 等级 → **必须出现**的判定档位（``None`` = 全 CP，没有「必须出现」这一说）。
COMBO_NEED: dict[int, str | None] = {
    0: "Miss",
    1: "Good",
    2: "Great",
    3: "Perfect",
    4: None,
}
#: 等级 → 可读名。
COMBO_NAMES: dict[int, str] = {0: "无", 1: "FC", 2: "FC+", 3: "AP", 4: "AP+"}
#: AP 的编号。``x小`` 写法蕴含它，所以要有个名字而不是散落的字面量 3。
COMBO_AP = 3
#: **会忽略百分比与星级**的等级。
#:
#: AP 只允许 CP/P ⇒ 普通音符一颗都不扣分，达成率只剩
#: ``101% - 25k / 加成理论分`` 这一串离散值（``k`` = 降成 P 的 break 数），
#: 用户给的百分比基本取不到；AP+ 更极端，只能是 101%。
#: 既然当约束用只会得到一个「差得很远」的结果，就干脆不当约束 ——
#: 直接给该等级下**最好**的那一份（101%），星级也一并按 5★ 走。
#: 要精确控制就写 ``x小``（见 :func:`parse_break_p`）。
COMBO_LOCKED: tuple[int, ...] = (3, 4)
#: 解析用的别名（全小写）→ 等级。
COMBO_ALIASES: dict[str, int] = {
    "0": 0, "none": 0, "无": 0, "不要求": 0,
    "1": 1, "fc": 1, "fullcombo": 1,
    "2": 2, "fc+": 2, "fcplus": 2, "fc＋": 2,
    "3": 3, "ap": 3, "allperfect": 3,
    "4": 4, "ap+": 4, "applus": 4, "ap＋": 4,
}


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------

def theory(zone_counts: dict[str, int]) -> tuple[int, int]:
    """``(常规理论分, BREAK 加成理论分)``。复用 ``judge_detail`` 的口径。"""
    return jd._theory_scores(zone_counts)


def achievement_int(counts: dict[str, int], normal: int, bonus: int) -> int:
    """按判定明细算达成率，单位 1/10000 %（``995989`` = 99.5989%）。

    **全整数运算** —— 用 :class:`~fractions.Fraction` 做精确有理数除法后
    向下取整，与游戏 ``(uint)`` 截断一致。

    :param normal: 常规理论分
    :param bonus: BREAK 加成理论分（为 0 时该项不参与）
    """
    if not normal:
        return 0
    ns = sum(
        counts[f"{z}{r}"] * SCORE[z][i] for z in ZONES for i, r in enumerate(RANKS)
    )
    bs = sum(counts[f"break{r}"] * BONUS[r] for r in RANKS)
    value = Fraction(ns, normal) * 100 + (Fraction(bs, bonus) if bonus else 0)
    return int(value * 10000)


def deluxscore(counts: dict[str, int]) -> int:
    """DX 分数。等价于 ``maimai.user_data.calculate_deluxscore``。"""
    return sum(counts[f"{z}{r}"] * DXPT[r] for z in ZONES for r in RANKS)


def star_borders(total_notes: int) -> tuple[list[int], int]:
    """``(各星级门槛, DX 满分)``。门槛用游戏原式 ``achieve × 满分 / 100 + 1``。"""
    full = total_notes * 3
    return [a * full // 100 + 1 for a in STAR_ACHIEVE], full


def stars_of(dx: int, borders: list[int]) -> int:
    """DX 分对应的星级（满足 ``dx ≥ border[j]`` 的最大 j）。"""
    return max(j for j in range(len(borders)) if dx >= borders[j])


def combo_status(counts: dict[str, int]) -> int:
    """连击状态（``PlayComboFlagID``）：0 无 / 1 FC / 2 FC+ / 3 AP / 4 AP+。"""
    great = sum(counts[f"{z}Great"] for z in ZONES)
    good = sum(counts[f"{z}Good"] for z in ZONES)
    miss = sum(counts[f"{z}Miss"] for z in ZONES)
    if miss:
        return 0
    if good:
        return 1                      # FullCombo
    if great:
        return 2                      # FullComboPlus
    perfect = sum(counts[f"{z}Perfect"] for z in ZONES)
    return 3 if perfect else 4        # AllPerfect / AllPerfectPlus


def combo_label(flag: int) -> str:
    """连击状态的可读名。"""
    return ("", "FC", "FC+", "AP", "AP+")[flag] if 0 <= flag <= 4 else ""


def max_combo(counts: dict[str, int]) -> int:
    """由判定明细推「本局最大连击」—— 有 Miss 时取**中间值**。

    连击规则（``GameScoreList.cs`` 约 615 行）：只有 ``TooFast`` / ``TooLate``
    （即 Miss）会 ``Combo = 0``，其余判定一律 ``Combo++``。所以：:

        设 N = 音符总数、k = Miss 数
        k == 0  ⇒  必然满连，maxCombo = N
        k  > 0  ⇒  N - k 颗非 Miss 音符被 k 颗 Miss 切成至多 k+1 段，
                   最长的一段落在 [ceil((N-k)/(k+1)), N-k] 之间。

    ``N - k`` 是「所有 Miss 都排在最后」这个极端；``ceil((N-k)/(k+1))`` 是
    「Miss 均匀铺开」的另一个极端。**取两者的中间值**，避免写出一个
    「有 3 个 Miss 却满连」或「Miss 恰好全在末尾」这种一眼假的连击数。

    （谱面里的音符顺序曲库不给，所以真值无从算起 —— 这里给的是区间中点。）
    """
    total = sum(counts.values())
    miss = sum(counts[f"{z}Miss"] for z in ZONES)
    if miss <= 0:
        return total
    rest = total - miss
    lowest = -(-rest // (miss + 1))           # ceil(rest / (miss + 1))
    return (lowest + rest) // 2


# ---------------------------------------------------------------------------
# 降级项
# ---------------------------------------------------------------------------

def _efficiency(item: tuple) -> float:
    """降级项的「每点 deficit 换多少 D」。用于排序与收尾时的取舍。"""
    zone, _rank, value, bonus, weight, _limit = item
    d = 100.0 * value / (item[6] or 1) + bonus / (item[7] or 1)
    return d / weight if weight else 0.0


def build_items(
    zone_counts: dict[str, int], l_max: int, b_max: int, hi: int,
    normal: int, bonus: int, *, allow_miss: bool = True, forbid: tuple[str, ...] = (),
) -> list[tuple]:
    """列出全部降级项，**并按预算裁剪每项的可用数量**。

    每项是 ``(zone, rank, scoreLoss, bonusLoss, dxLoss, limit, normal, bonus)``。
    裁剪上限不是可有可无的优化：二分拆包的数量随 ``limit`` 取对数增长，
    不裁剪会让背包的包数翻倍、DP 直接慢一倍。

    :param allow_miss: ``False`` 时不列 Miss 项。见 :func:`solve` 的 ``combo``。
    :param forbid: 要**剔除**的判定档位名（如 ``("Miss", "Good")``）。
        combo 等级就是靠它落地的：想要 FC+ 就不能用 Good 与 Miss 来凑分。
    """
    items: list[tuple] = []

    def effective(limit: int, value: int, bl: int, weight: int) -> int:
        if value:
            limit = min(limit, l_max // value)
        if bl:
            limit = min(limit, b_max // bl)
        return min(limit, hi // weight)

    def allowed(rank: str) -> bool:
        return rank not in forbid and (allow_miss or rank != "Miss")

    for z in NORMAL_ZONES:
        n = zone_counts[z]
        if not n:
            continue
        for i in (2, 3, 4):                      # Great / Good / Miss
            if not allowed(RANKS[i]):
                continue
            value = SCORE[z][0] - SCORE[z][i]
            weight = 2 if i == 2 else 3
            items.append((z, RANKS[i], value, 0, weight,
                          effective(n, value, 0, weight), normal, bonus))
    if zone_counts["break"]:
        n = zone_counts["break"]
        for i, weight in ((1, 1), (2, 2), (3, 3), (4, 3)):   # P / Great / Good / Miss
            if not allowed(RANKS[i]):
                continue
            value = SCORE["break"][0] - SCORE["break"][i]
            bl = 100 - BONUS[RANKS[i]]
            items.append(("break", RANKS[i], value, bl, weight,
                          effective(n, value, bl, weight), normal, bonus))
    return items


# ---------------------------------------------------------------------------
# 求解
# ---------------------------------------------------------------------------

@dataclass
class Solution:
    """一次求解的结果。"""

    counts: dict[str, int]
    """完整的判定明细（25 个键，可直接上传）。"""

    achievement: int
    """实际达成率，单位 1/10000 %。"""

    deluxscore: int
    stars: int

    gap: int
    """``achievement - target``，单位 1/10000 %。**恒 ≥ 0**（向上估计）。
    ``0`` = 顶到了目标值；``≤ TOLERANCE`` = 落在产品口径的容差带内。"""

    exact: bool
    """``True`` = 已证明它就是「≥ 目标的最小可达值」。
    ``False`` = 搜索提前收尾（见 :data:`MAX_STATES` / :data:`TIME_BUDGET`），
    结果仍然合法（达成率 ≥ 目标、星级正确），只是未必最紧。"""

    deficit: int
    """DX 满分 - DX 分。"""

    @property
    def within_tolerance(self) -> bool:
        """达成率与目标的差距是否在容差带内（产品口径的「够用」）。"""
        return self.gap <= TOLERANCE


def solve(
    zone_counts: dict[str, int], target: int, want_star: int,
    combo: int = COMBO_ANY,
) -> tuple[Solution | None, str | None]:
    """求「达成率 ≥ ``target`` 且星级为 ``want_star``」的最小可达达成率。

    :param target: 目标达成率，单位 1/10000 %（``1000000`` = 100.0%）
    :param want_star: 目标星级 0-5
    :param combo: 连击等级（0-4），或 :data:`COMBO_ANY` 表示不限。
        **是「恰好等于」而不是「不低于」** —— 想要 FC 就必须留一颗 Good，
        想要 AP 就必须留一颗 Perfect，否则会被判成更高一档。
        见 :data:`COMBO_FORBID` / :data:`COMBO_NEED`。
    :returns: ``(解, 错误文案键)``。解为 ``None`` 时第二个元素是失败原因。

    做法：把「D ≤ Dmax 且 deficit ≤ hi 下最大化 D」写成
    ``dp[(L, B)] = 最少的 deficit``，其中 ``L`` 是常规分亏损、``B`` 是加成亏损。
    状态按值上限联合裁剪（只判 ``L ≤ Lmax`` 与 ``B ≤ Bmax`` 会漏掉大量实际超限
    的状态），并对每个 ``(L, B)`` 只留最少 deficit。

    **精确命中就提前收尾**：``D == Dmax`` 已经是「≤ Dmax」的上确界，
    必然就是最优解，没必要继续把背包填完。绝大多数目标值都能被精确命中
    （分值粒度足够细），所以这一步把常见输入的耗时从秒级压到毫秒级。
    """
    normal, bonus = theory(zone_counts)
    total_notes = sum(zone_counts[t] for t in ZONES)
    if not normal or not total_notes:
        return None, "estimate.empty_chart"

    forbid = COMBO_FORBID.get(combo, ())
    need = COMBO_NEED.get(combo)

    borders, full = star_borders(total_notes)
    hi = full - borders[want_star]
    lo = (full - borders[want_star + 1]) if want_star < MAX_STAR else -1
    # 该区间为空只可能是「满星时下一档门槛超过满分」—— 由 STAR_ACHIEVE 的取值
    # 与 total_notes > 0 可证不可能发生。留着是**防御**：真发生了也走同一个
    # 文案，不另立一个永远显示不出来的键。
    if lo >= hi:
        return None, "estimate.no_solution"

    d_num = ACH_MAX - target                     # Dmax，单位 1/10000 %
    if d_num < 0:
        return None, "estimate.too_high"

    l_max = d_num * normal // 1000000
    b_max = min(d_num * bonus // 10000, 100 * zone_counts["break"])
    items = build_items(zone_counts, l_max, b_max, hi, normal, bonus, forbid=forbid)

    # 联合裁剪：100L/normal + B/bonus ≤ d_num/10000
    #          ⟺ 1e6·L·bonus + 1e4·B·normal ≤ d_num·normal·bonus
    # 下面统一用整数 ``obj = 1e6·L·bonus + 1e4·B·normal`` 当目标值 —— 它与
    # 达成率是**单调反向**的（obj 越大 ⇒ 达成率越低），且是纯整数，收尾扫描
    # 里对几十万个状态逐个比较也不怕。
    #   obj ≤ u_bound          ⟺ 达成率 ≥ 目标
    #   obj ≥ u_low            ⟺ 达成率 ≤ 目标 + TOLERANCE
    u_bound = d_num * normal * bonus
    u_low = max(0, d_num - TOLERANCE) * normal * bonus
    u_prefer = max(0, d_num - PREFER_TOLERANCE) * normal * bonus

    #: 可「免费补位」的音符总数 —— 就是补位的容量上限（见 :func:`_pad_to_band`）。
    #: ⚠️ 用 :data:`PAD_ZONES` 而不是 :data:`NORMAL_ZONES`：slide 没有小 P，
    #: 不能算进容量，否则补位会「成功」但补出游戏里不存在的判定。
    pad_notes = sum(zone_counts[z] for z in PAD_ZONES)
    pad_idx = tuple(ZONES.index(z) for z in PAD_ZONES)

    #: combo=3（AP）要求「至少一颗 Perfect」。Perfect 不走降级项 —— 它是
    #: CP→P 的**免费补位**（同分），所以只能在这里要求「至少补一颗」。
    #: combo=4（AP+）反过来：全 CP，**一颗都不许补**。
    pad_min = 1 if combo == 3 else 0
    pad_max = 0 if combo == 4 else None

    def band_ok(deficit: int, zone_used: tuple[int, ...]) -> bool:
        """免费补位之后，deficit 能不能落进目标星级区间 ``(lo, hi]``。"""
        if pad_max == 0:
            return lo < deficit <= hi          # AP+：不许补位，deficit 必须原样落区间
        free = pad_notes - sum(zone_used[i] for i in pad_idx)
        need = max(pad_min, lo + 1 - deficit, 0)
        return need <= free and deficit + need <= hi

    zone_index = {z: i for i, z in enumerate(ZONES)}

    # dp[(L, B)] = (最少 deficit, 各降级项用量, 各音符类型已降级数)
    #
    # **combo 等级「必须出现」的档位靠起点钉住**：不空手起手，而是预先吃进
    # 一颗该档位的降级。这样「≥ 1 颗」就等价于「所有可达状态都满足」，
    # 不必给 DP 再加一维 —— 加一维会让状态数与内存直接翻倍，而这里只多几个起点。
    dp: dict[tuple[int, int], tuple[int, tuple[int, ...], tuple[int, ...]]] = {}
    #: AP 的「必须有 P」不走这里：它的 P 由免费补位产生（见 ``pad_min``），
    #: 而且曲库里 break 的 P 会真的扣 25 点加成，预吃一颗反而把解做差。
    seed_rank = None if need in (None, "Perfect") else need

    if seed_rank is None:
        dp[(0, 0)] = (0, (0,) * len(items), (0,) * len(ZONES))
    else:
        for idx, item in enumerate(items):
            if item[1] != seed_rank:
                continue
            value, bl, weight = item[2], item[3], item[4]
            if value > l_max or bl > b_max or weight > hi:
                continue
            if 1000000 * value * bonus + 10000 * bl * normal > u_bound:
                continue
            uses = [0] * len(items)
            uses[idx] = 1
            zu = [0] * len(ZONES)
            zu[zone_index[item[0]]] = 1
            key = (value, bl)
            seen = dp.get(key)
            if seen is None or weight < seen[0]:
                dp[key] = (weight, tuple(uses), tuple(zu))
        if not dp:
            # 一颗都塞不下 ⇒ 这个 combo 等级在这个目标下不可能达成
            return None, "estimate.combo_unreachable"

    # 包按「包内数量升序」处理：这样一旦触到上限提前收尾，**每种降级项都拿到了
    # 少量配额**，而不是「前几种吃满、后面一个没有」—— 后者会让收尾结果很难看。
    packs: list[tuple[int, int]] = []
    for idx, item in enumerate(items):
        limit, k = item[5], 1
        while limit > 0:
            take = min(k, limit)
            packs.append((idx, take))
            limit -= take
            k *= 2
    packs.sort(key=lambda p: (p[1], p[0]))

    started = time.monotonic()
    exact = True
    #: 达标的解 ``(obj, L, B, deficit, uses, zone_used)``。取到就收工 ——
    #: 用户口径是「0.1 以内即可」，没必要为最后那一点点差距把背包填满。
    hit: tuple | None = None

    def consider(obj, l_val, b_val, deficit, uses, zu) -> None:
        """落进容差带就记成候选（留 obj 最大的那个，即最贴近目标的）。"""
        nonlocal hit
        if obj < u_low or (hit is not None and obj <= hit[0]):
            return
        if band_ok(deficit, zu):
            hit = (obj, l_val, b_val, deficit, uses, zu)

    # 起点本身也可能是达标解（combo 等级预吃的那一颗就已经落进带内）
    for (l_val, b_val), (deficit, uses, zu) in dp.items():
        consider(1000000 * l_val * bonus + 10000 * b_val * normal,
                 l_val, b_val, deficit, uses, zu)

    for idx, take in packs:
        zone, _rank, value, bl, weight, _limit, _n, _b = items[idx]
        add_l, add_b, add_w = value * take, bl * take, weight * take
        zone_i = zone_index[zone]
        fresh: dict[tuple[int, int], tuple[int, tuple[int, ...], tuple[int, ...]]] = {}
        for (cur_l, cur_b), (deficit, uses, zone_used) in dp.items():
            if zone_used[zone_i] + take > zone_counts[zone]:
                continue
            new_l, new_b, new_w = cur_l + add_l, cur_b + add_b, deficit + add_w
            if new_l > l_max or new_b > b_max or new_w > hi:
                continue
            obj = 1000000 * new_l * bonus + 10000 * new_b * normal
            if obj > u_bound:
                continue
            new_uses = list(uses)
            new_uses[idx] += take
            new_zone = list(zone_used)
            new_zone[zone_i] += take
            key = (new_l, new_b)
            seen = fresh.get(key)
            if seen is None or new_w < seen[0]:
                fresh[key] = (new_w, tuple(new_uses), tuple(new_zone))
            consider(obj, new_l, new_b, new_w, tuple(new_uses), tuple(new_zone))
        for key, value_ in fresh.items():
            seen = dp.get(key)
            if seen is None or value_[0] < seen[0]:
                dp[key] = value_
        if hit is not None:
            if hit[0] >= u_prefer:
                break                            # 够紧，直接收工
            if time.monotonic() - started > GRACE:
                break                            # 已达标但不够紧，不再耗下去
        if len(dp) > MAX_STATES or time.monotonic() - started > TIME_BUDGET:
            exact = False
            logger.info(
                "估分：组合搜索提前收尾（状态 %d，已用 %.2fs）", len(dp), time.monotonic() - started
            )
            break

    if hit is not None:
        best = (hit[0], hit[1], hit[2], hit[3], hit[4])
    else:
        # 没取到达标解 ⇒ 在已探索到的状态里挑最紧的那个（obj 最大且 ≤ u_bound）。
        # 这里**必须用整数**：要对全部状态跑一遍，用 Fraction 会平白多出几十万次
        # 有理数构造与比较（实测占整个 solve 的三分之一）。
        best = None
        for (l_val, b_val), (deficit, uses, zu) in dp.items():
            obj = 1000000 * l_val * bonus + 10000 * b_val * normal
            if obj > u_bound:
                continue
            if not band_ok(deficit, zu):
                continue
            if best is None or obj > best[0]:
                best = (obj, l_val, b_val, deficit, uses)
    if best is None:
        # combo 等级是「恰好」约束，取不到解时多半是它卡住的 —— 换个文案更好懂
        return None, ("estimate.combo_unreachable" if combo != COMBO_ANY
                      else "estimate.no_solution")

    _obj, _l, _b, deficit, uses = best
    counts = {f"{z}{r}": 0 for z in ZONES for r in RANKS}
    for idx, item in enumerate(items):
        counts[f"{item[0]}{item[1]}"] += uses[idx]
    for z in ZONES:
        used = sum(counts[f"{z}{r}"] for r in RANKS[1:])
        counts[f"{z}CriticalPerfect"] = zone_counts[z] - used

    # CP → P 不改变达成率（同分），但每颗 -1 DX，正好用来把 deficit 抬进星级区间。
    # ⚠️ 只对**非 break** 这么做：break 的 CP 加成 100 / P 只有 75，
    # 转换会真的扣掉达成率（见 judge_detail.BREAK_TIERS）。
    counts, _padded = _pad_to_band(counts, lo, full, minimum=pad_min, maximum=pad_max)

    # 安全网：combo 等级是「恰好」约束，补位/降级任何一步出岔子都在这里拦下。
    # 宁可报「取不到解」，也不能给用户一份判定与连击状态对不上的记录。
    if combo != COMBO_ANY and combo_status(counts) != combo:
        return None, "estimate.combo_unreachable"

    dx = deluxscore(counts)
    stars = stars_of(dx, borders)
    achievement = achievement_int(counts, normal, bonus)
    if achievement == target:
        exact = True                             # 已经顶到目标，不可能再低 ⇒ 必然最优
    return Solution(
        counts=counts,
        achievement=achievement,
        deluxscore=dx,
        stars=stars,
        gap=achievement - target,
        exact=exact,
        deficit=full - dx,
    ), None


def _pad_to_band(
    counts: dict[str, int], lo: int, full: int,
    *, minimum: int = 0, maximum: int | None = None,
) -> tuple[dict[str, int], int]:
    """把 CP 降成 P，直到 deficit 落进目标星级区间的下界之上。

    ``lo`` 是「再高一颗星」的门槛，所以要 ``deficit > lo``。

    :param minimum: 至少要补几颗。combo=3（AP）用它保证「至少一颗 Perfect」。
    :param maximum: 最多补几颗。combo=4（AP+）传 ``0`` —— 全 CP 一颗都不许动。
    :returns: ``(新的判定明细, 实际补了几颗)``。

    ⚠️ **只动 :data:`PAD_ZONES`（tap / hold / touch），两个都不能碰：**

    * **break** —— CP 加成 100、P 只有 75（``judge_detail.BREAK_TIERS``），
      降成 P 会真的扣达成率，那就不再是「免费补位」，而是一个要进背包
      一起权衡的取舍；
    * **slide** —— 它根本没有小 P（``judgeParamTbl[2]`` 的 PERFECT 区间是
      退化的空区间，见 :data:`PAD_ZONES`）。降出来的是游戏里打不出的判定。
    """
    deficit = full - deluxscore(counts)
    need = max(minimum, lo + 1 - deficit, 0)
    if maximum is not None:
        need = min(need, maximum)
    if need <= 0:
        return counts, 0
    left = need
    for z in PAD_ZONES:
        take = min(left, counts[f"{z}CriticalPerfect"])
        counts[f"{z}CriticalPerfect"] -= take
        counts[f"{z}Perfect"] += take
        left -= take
        if not left:
            break
    return counts, need - left


# ---------------------------------------------------------------------------
# ``x小`` / ``x小P``：AP 下用 break 的小 P 数直接钉死三个数
# ---------------------------------------------------------------------------

def build_break_p_solution(
    zone_counts: dict[str, int], count: int, normal: int, bonus: int,
    borders: list[int], full: int,
) -> Solution | None:
    """``x小`` 的构造：break 恰好 ``count`` 颗小 P，另加 **1 颗**普通小 P 满足 AP。

    为什么普通小 P 只要 1 颗：AP 的定义要求「至少一颗非 CP」（全 CP 就是 AP+ 了），
    而普通音符的 CP→P **不改变达成率**（同分），所以 1 颗就是最小、最干净的 AP ——
    多补一颗只会白掉 1 点 DX 分。正因如此，``count`` 与
    「达成率 / DX 分 / 星级」是**一一对应**的，写一个数就把三个都指定了。

    ⚠️ **普通小 P 只放在 :data:`PAD_ZONES`（tap / hold / touch）**：
    slide 根本没有小 P（``judgeParamTbl[2]`` 的 PERFECT 区间是退化的空区间）。

    :param count: break 的小 P 数，取值 ``0 .. zone_counts["break"]``
    :returns: ``Solution``；``count`` 超范围 / 没有可放普通小 P 的音符时 ``None``
    """
    if count < 0 or count > zone_counts["break"]:
        return None
    pad_zone = next((z for z in PAD_ZONES if zone_counts[z]), None)
    if pad_zone is None:
        return None

    counts = {f"{z}{r}": 0 for z in ZONES for r in RANKS}
    for z in ZONES:
        counts[f"{z}CriticalPerfect"] = zone_counts[z]
    counts[f"{pad_zone}CriticalPerfect"] -= 1
    counts[f"{pad_zone}Perfect"] += 1
    if count:
        counts["breakCriticalPerfect"] -= count
        counts["breakPerfect"] += count

    dx = deluxscore(counts)
    return Solution(
        counts=counts,
        achievement=achievement_int(counts, normal, bonus),
        deluxscore=dx,
        stars=stars_of(dx, borders),
        gap=0,                                   # 由调用方按真实目标回填
        exact=True,
        deficit=full - dx,
    )


# ---------------------------------------------------------------------------
# 对外的估分入口
# ---------------------------------------------------------------------------

@dataclass
class Estimate:
    """一次估分的完整结果 —— 也是**可上传数据**的来源。"""

    song_id: int
    title: str
    chart_type: str
    difficulty_index: int
    difficulty_name: str
    level: Any
    charter: Any
    zone_counts: dict[str, int]
    normal_theory: int
    bonus_theory: int
    target: int
    solution: Solution
    want_stars: int
    combo: int
    """要求的 combo 等级（0-4），或 :data:`COMBO_ANY`。"""
    break_p: int | None
    """``x小`` 指定的 break 小 P 数；没用这种写法时为 ``None``。"""
    borders: list[int]
    max_deluxscore: int
    created_at: float = field(default_factory=time.time)

    @property
    def counts(self) -> dict[str, int]:
        return self.solution.counts

    @property
    def note_total(self) -> int:
        return sum(self.zone_counts.values())

    @property
    def combo_flag(self) -> int:
        """这份分布实际会显示的连击状态（``PlayComboFlagID``）。"""
        return combo_status(self.counts)

    def payload(self) -> dict[str, Any]:
        """可直接喂给上传工具的成绩 JSON（字段名与 ``live_upload.py`` 一致）。"""
        return {
            "musicId": self.song_id,
            "level": self.difficulty_index,
            "achievement": self.solution.achievement,
            "deluxscoreMax": self.solution.deluxscore,
            "comboStatus": self.combo_flag,
            "syncStatus": 0,
            "maxCombo": max_combo(self.counts),
            "isClear": True,
            "trackNo": 1,
            "noteCounts": dict(self.counts),
        }


def parse_percent(text: str) -> int | None:
    """解析百分比写法 → 单位 1/10000 % 的整数。不认识返回 ``None``。

    收 ``100`` / ``100.0`` / ``100.00`` / ``100.0%``；**不收**负数与
    ``nan``/``inf``。小数位多于 4 位直接截断（达成率精度就是 4 位）。
    """
    if not isinstance(text, str):
        return None
    raw = text.strip().rstrip("%").strip()
    if not raw:
        return None
    if not all(c.isdigit() or c == "." for c in raw):
        return None
    if raw.count(".") > 1:
        return None
    whole, _, frac = raw.partition(".")
    frac = (frac + "0000")[:4]
    if not whole and not frac.strip("0"):
        return None
    try:
        return int(whole or "0") * 10000 + int(frac or "0")
    except ValueError:
        return None


def parse_stars(text: str) -> int | None:
    """解析星级写法 → 0-5。收 ``2`` / ``2星`` / ``★★``。

    空串返回 ``0`` —— 「没要求星级」与「要求 0★」是同一件事（0 就是最低档），
    所以省略参数不需要另设一个哨兵值。
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return 0
    if set(raw) == {"★"}:
        return len(raw) if 1 <= len(raw) <= MAX_STAR else None
    raw = raw.rstrip("星").strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if 0 <= value <= MAX_STAR else None


def parse_combo(text: str) -> int | None:
    """解析 combo 等级 → 0-4。收 ``0``-``4`` / ``FC`` / ``FC+`` / ``AP`` / ``AP+``。

    大小写不敏感，也收全角 ``＋``。空串返回 :data:`COMBO_ANY`（不限）——
    参数省略时走的就是这条路。
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return COMBO_ANY
    return COMBO_ALIASES.get(raw.lower())


#: ``x小`` / ``x小P`` 写法。``小`` 后面可跟一个 ``p`` / ``P``。
_BREAK_P_RE = re.compile(r"^(\d+)\s*小\s*[pP]?$")


def parse_break_p(text: str) -> int | None:
    """解析 ``x小`` / ``x小P`` → **break 的 P 判定数**。不认识返回 ``None``。

    这是 **AP 专用的另一种写法**（用户口径：``x小`` = 「AP 时 break 的 P
    判定数」）。AP 只允许 CP/P，普通音符一颗都不扣分，于是达成率、DX 分、
    星级三者**全部**由「break 降了几颗小 P」决定 —— 所以写一个 ``3小``
    就等于把这三个数一次钉死，见 :func:`build_break_p_solution`。

    ⚠️ 与星级写法**不可能撞车**：``parse_stars`` 收 ``3`` / ``3星`` / ``★★★``，
    ``3小`` 既不是纯数字也不是 ``★``，两边互不认。
    """
    if not isinstance(text, str):
        return None
    matched = _BREAK_P_RE.match(text.strip())
    return int(matched.group(1)) if matched else None


def estimate(
    song_id: str, difficulty: str, percent: str = "", stars: str = "",
    combo: str = "", break_p: str = "",
) -> tuple[Estimate | None, str | None]:
    """``/估分`` 的主入口。

    :param percent: 目标达成率；**AP / AP+ 下会被忽略**（见下），也可留空。
    :param stars: 星级；空串 = 不限（按 0 处理）。AP / AP+ 下同样被忽略。
    :param combo: combo 等级；空串 = 不限（:data:`COMBO_ANY`）。
    :param break_p: ``x小`` / ``x小P`` 写法 —— **AP 下 break 的小 P 数**。
        给了它，百分比与星级就都不需要了：它把达成率 / DX 分 / 星级一次钉死
        （见 :func:`build_break_p_solution`）。它同时**蕴含 AP**。
    :returns: ``(Estimate, None)`` 或 ``(None, 文案)``。
        **失败时返回的是已经渲染好的用户可见文案**（不是键）—— 这样调用方
        （:func:`estimate_reply` / :mod:`liz_bot.estimate_image` /
        ``command_router._is_failure``）都不必再知道每个键要什么占位符。
        ``judge.no_chart`` 的占位符要列可用难度，只有这里拿得到，也只能在这里渲染。

    ⚠️ **AP / AP+ 忽略百分比与星级**（见 :data:`COMBO_LOCKED`）：这两档把可用
    判定档位锁死之后，达成率由「物量 + break 数」唯一决定 —— 普通音符一颗都
    不扣分，只有 break 降成 P 才会扣，粒度是 ``25 / 加成理论分``。以 147 为例，
    AP 的达成率只能是 ``101% - 25k/1400``（``k`` = break 的 P 数），最接近
    100.0% 的可行值是 100.75%。既然用户给的数取不到，就干脆别当约束用，
    直接给该等级下**最好**的那一份（``101%``）。
    想精确控制就写 ``x小``（见 :param:`break_p`）。
    """
    index = jd.resolve_difficulty(difficulty)
    if index is None:
        return None, replies.text("judge.bad_difficulty", value=difficulty)

    # combo 先解析：它决定了百分比还要不要
    want_combo = parse_combo(combo)
    if want_combo is None:
        return None, replies.text("estimate.bad_combo", value=combo)

    want_break_p = parse_break_p(break_p) if break_p.strip() else None
    if break_p.strip() and want_break_p is None:
        return None, replies.text("estimate.bad_break_p", value=break_p)
    if want_break_p is not None and want_combo not in (COMBO_ANY, COMBO_AP):
        # ``x小`` 就是 AP 的写法，与别的 combo 等级互斥
        return None, replies.text("estimate.combo_conflict",
                                  combo=COMBO_NAMES.get(want_combo, combo))

    want_star = parse_stars(stars)
    if want_star is None:
        return None, replies.text("estimate.bad_stars", value=stars)

    if want_break_p is not None:
        # 走 x小 专用路径：百分比与星级都由它推出，不参与求解
        target, want_star, want_combo = 0, MAX_STAR, COMBO_AP
    elif want_combo in COMBO_LOCKED:
        # AP / AP+：百分比与星级都被物量锁死，给的数一律忽略
        target, want_star = ACH_MAX, MAX_STAR
    elif not percent.strip():
        return None, replies.text("estimate.need_percent")
    else:
        target = parse_percent(percent)
        if target is None or target < ACH_MIN:
            return None, replies.text("estimate.bad_percent", value=percent)
        if target > ACH_MAX:
            # 写法合法但超出理论上限（101%）—— 与「看不懂」分开，提示才有用。
            return None, replies.text("estimate.too_high")

    try:
        hits = song_query.select_song(select_data=song_id, select_type=song_query.BY_ID)
    except (OSError, ValueError):
        logger.exception("估分：曲库载入失败 song_id=%r", song_id)
        return None, replies.text("song.data_error")
    if not hits:
        return None, replies.text("song.not_found")

    song = hits[0]["song"]
    charts = song.get("charts") or []
    if index >= len(charts):
        names = jd.difficulty_names()
        levels = song.get("level") or []
        available = "\n".join(
            replies.text("judge.available_line", name=names[i], level=levels[i])
            for i in range(len(charts))
            if i < len(levels) and i < len(names)
        )
        return None, replies.text(
            "judge.no_chart",
            difficulty=names[index] if index < len(names) else str(index),
            available=available,
        )

    zone_counts = jd._counts(charts[index])
    normal, bonus = theory(zone_counts)
    borders, full = star_borders(sum(zone_counts.values()))

    def wrap(solution: Solution, target_: int, star_: int) -> Estimate:
        """两条路径（``x小`` / 背包搜索）共用的结果装配。"""
        names_ = jd.difficulty_names()
        levels_ = song.get("level") or []
        chart_ = charts[index]
        return Estimate(
            song_id=song.get("id") if isinstance(song.get("id"), int) else int(song_id),
            title=song.get("title") or "",
            chart_type=song.get("type") or "",
            difficulty_index=index,
            difficulty_name=names_[index] if index < len(names_) else str(index),
            level=levels_[index] if index < len(levels_) else None,
            charter=chart_.get("charter") if isinstance(chart_, dict) else None,
            zone_counts=zone_counts,
            normal_theory=normal,
            bonus_theory=bonus,
            target=target_,
            solution=solution,
            want_stars=star_,
            combo=want_combo,
            break_p=want_break_p,
            borders=borders,
            max_deluxscore=full,
        )

    if want_break_p is not None:
        # ``x小``：break 恰好这么些小 P ⇒ 达成率 / DX 分 / 星级全部确定，
        # 不需要（也不该）再走背包搜索。target 直接取算出来的达成率，
        # 于是 gap == 0、within_tolerance 为真。
        solution = build_break_p_solution(
            zone_counts, want_break_p, normal, bonus, borders, full)
        if solution is None:
            return None, replies.text("estimate.bad_break_p", value=break_p)
        return wrap(solution, solution.achievement, solution.stars), None

    solution, err = solve(zone_counts, target, want_star, want_combo)
    if solution is None:
        if err == "estimate.combo_unreachable":
            return None, replies.text("estimate.combo_unreachable",
                                      combo=COMBO_NAMES.get(want_combo, str(want_combo)))
        return None, replies.text(err or "estimate.no_solution")

    return wrap(solution, target, want_star), None


# ---------------------------------------------------------------------------
# 文字版回复
# ---------------------------------------------------------------------------

#: 判定明细表的列宽（显示宽度）。第一列是音符类型（左对齐），其余右对齐。
_TABLE_WIDTHS = (5, 4, 4, 4, 3, 3, 3)
#: 判定明细表的列头（第一列是行标签，ASCII 之外的部分走文案）。
_TABLE_HEADS = ("CP", "P", "Gr", "Gd", "Ms")
#: 可上传字段名的对齐宽度（``achievement`` / ``deluxscoreMax`` 都是 13 格）。
_UPLOAD_NAME_WIDTH = 13


def _pct(value: int) -> str:
    """达成率（单位 1/10000 %）→ ``99.5989``。**整数运算**，不经过浮点。"""
    return f"{value // 10000}.{value % 10000:04d}"


def _table(est: Estimate) -> str:
    """判定明细表：行 = 音符类型，列 = 数量 + 5 个判定档位。"""
    counts = est.counts
    dash = replies.text("judge.placeholder")

    rows: list[list[str]] = [[
        replies.text("estimate.table_zone"),
        replies.text("estimate.table_head"),
        *_TABLE_HEADS,
    ]]
    for zone in ZONES:
        if not est.zone_counts[zone]:
            # 该谱面没有这类音符（SD 谱没有 touch）—— 整行记 '-'
            rows.append([zone, *([dash] * (len(_TABLE_HEADS) + 1))])
            continue
        rows.append([
            zone,
            str(est.zone_counts[zone]),
            *(str(counts[f"{zone}{rank}"]) for rank in RANKS),
        ])

    lines = []
    for row in rows:
        # 第一列（行标签）左对齐，数字列右对齐
        lines.append(" ".join(
            text_layout.pad(cell, width, right=index > 0)
            for index, (cell, width) in enumerate(zip(row, _TABLE_WIDTHS))
        ))
    return "\n".join(lines)


def combo_summary(est: Estimate) -> tuple[str, str]:
    """``estimate.combo_req`` 的两个占位符 → ``(要求的等级, 实际判定)``。

    文字版与图片版共用，免得一边显示「不限」另一边显示空白。
    """
    label = combo_label(est.combo_flag) or COMBO_NAMES[0]
    want = "不限" if est.combo == COMBO_ANY else COMBO_NAMES.get(est.combo, "")
    return want, label


def ignored_note(est: Estimate) -> str | None:
    """百分比 / 星级被忽略时的说明行；没忽略则 ``None``。

    结果行里的「目标」不是用户给的数，而是算法自己取的上限（101%）。
    不说明白，用户会以为那就是他要的百分比。

    ``x小`` 更彻底：百分比与星级**都由 break 的小 P 数推出来**，所以走另一句
    文案（见 ``estimate.break_p_note``）。
    """
    if est.break_p is not None:
        return replies.text("estimate.break_p_note", count=est.break_p)
    if est.combo not in COMBO_LOCKED:
        return None
    return replies.text("estimate.ignored", combo=COMBO_NAMES[est.combo])


def _upload_lines(est: Estimate) -> list[str]:
    """「可上传字段」那一段 —— 字段名与 :meth:`Estimate.payload` 一一对应。

    一行一个字段（而不是挤成两三行）：字段名最长 13 格，一行一个之后
    整段最宽也只有 23 格，离 40 格的行宽上限很远，可读性反而最好。
    ``musicId`` / ``level`` 不重复列 —— 表头里就是它们。
    """
    counts = est.counts
    flag = combo_status(counts)
    combo = (
        replies.text("estimate.combo", flag=flag, label=combo_label(flag))
        if flag else replies.text("estimate.combo_none")
    )
    values = (
        ("achievement", est.solution.achievement),
        ("deluxscoreMax", est.solution.deluxscore),
        ("comboStatus", combo),
        ("maxCombo", max_combo(counts)),
        ("syncStatus", 0),
        ("isClear", "true"),
        ("trackNo", 1),
    )
    return [
        replies.text("estimate.upload_title"),
        *(
            replies.text("estimate.upload_line",
                         name=f"{name:<{_UPLOAD_NAME_WIDTH}}", value=value)
            for name, value in values
        ),
    ]


def estimate_reply(
    song_id: str, difficulty: str, percent: str = "", stars: str = "",
    combo: str = "", break_p: str = "", *, session_key: str | None = None,
) -> str:
    """``/估分 <歌曲id> <难度> <百分比> [星级] [combo]`` 的回复。

    成功时把结果**存进一轮缓存**（见 :data:`CACHE`）—— 目前只存不取，
    留着接口等产品决定下一轮拿它做什么。

    :param stars: 省略时按 ``0``（不指定星级要求）。
    :param combo: 省略时按「不限」（:data:`COMBO_ANY`）。
    :param break_p: ``x小`` / ``x小P`` 写法（AP 下 break 的小 P 数）。
    :param session_key: 会话标识，作为缓存键；``None`` / 空串则不缓存。
    :return: 渲染好的文本；参数不认识 / 取不到解时是失败文案
    """
    est, err = estimate(song_id, difficulty, percent, stars, combo, break_p)
    if est is None:
        return err or replies.text("estimate.no_solution")

    CACHE.put(session_key, est)

    sol = est.solution
    dash = replies.text("judge.placeholder")
    header = replies.text(
        "judge.header",
        title=est.title,
        difficulty=est.difficulty_name,
        level=est.level if est.level is not None else dash,
        charter=est.charter if est.charter else dash,
    )
    gap_line = (
        replies.text("estimate.gap_zero") if not sol.gap
        else replies.text("estimate.gap_note", gap=_pct(sol.gap))
    )
    # gap 超容差 ⇒ 必须显式告警。这只在「可达值很稀疏」时发生 —— 典型是
    # combo 等级把可用档位卡死之后（如 AP 只允许 CP/P，普通音符一颗都不扣）。
    # 用户要的是能直接上传的记录，不告警他会以为这就是他要的百分比。
    gap_warn = (
        replies.text("estimate.gap_warn", gap=_pct(sol.gap))
        if sol.gap > TOLERANCE else None
    )
    combo_want, combo_actual = combo_summary(est)
    return "\n".join(filter(None, [
        header,
        replies.text("estimate.result",
                     target=_pct(est.target), actual=_pct(sol.achievement)),
        replies.text("estimate.stars", stars=sol.stars, want=est.want_stars,
                     dx=sol.deluxscore, max_dx=est.max_deluxscore),
        replies.text("estimate.combo_req", combo=combo_want, label=combo_actual),
        ignored_note(est),
        replies.text("estimate.scale",
                     normal=est.normal_theory, bonus=est.bonus_theory),
        _table(est),
        *_upload_lines(est),
        gap_line,
        gap_warn,
        replies.text("estimate.unit"),
    ]))


# ---------------------------------------------------------------------------
# 一轮缓存
# ---------------------------------------------------------------------------

#: 估分结果的存活时间（秒）。**「保存一轮对话」** —— 下一条消息还能拿到它。
DEFAULT_TTL = 300.0
#: 最多同时记住多少个会话。
DEFAULT_CAPACITY = 500


class EstimateCache:
    """按会话键缓存最近一次估分结果。**线程安全**。

    目前**只存不取** —— 产品上还没决定下一轮拿它做什么（改参数重算？
    导出 JSON？），所以先把接口留在这里，行为保持为空。
    取用请调 :meth:`get`；要消费就调 :meth:`take`（取走并删除）。
    """

    def __init__(self, ttl: float = DEFAULT_TTL, capacity: int = DEFAULT_CAPACITY):
        self.ttl = ttl
        self.capacity = capacity
        self._items: dict[str, Estimate] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        dead = [k for k, v in self._items.items() if now - v.created_at > self.ttl]
        for key in dead:
            del self._items[key]

    def put(self, key: str | None, value: Estimate) -> None:
        """记住某会话的最新估分。``key`` 为空则不存（无状态调用）。"""
        if not key:
            return
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            while len(self._items) >= self.capacity:
                oldest = min(self._items, key=lambda k: self._items[k].created_at)
                del self._items[oldest]
            value.created_at = now
            self._items[key] = value

    def get(self, key: str | None) -> Estimate | None:
        """取（**不删除**）某会话的估分；没有或已超时返回 ``None``。"""
        if not key:
            return None
        now = time.monotonic()
        with self._lock:
            value = self._items.get(key)
            if value is None:
                return None
            if now - value.created_at > self.ttl:
                del self._items[key]
                return None
            return value

    def take(self, key: str | None) -> Estimate | None:
        """取走并删除（消费一轮）。"""
        if not key:
            return None
        with self._lock:
            return self._items.pop(key, None)

    def drop(self, key: str | None) -> None:
        if not key:
            return
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            self._sweep(time.monotonic())
            return len(self._items)


#: 进程级的估分缓存。见 :class:`EstimateCache`。
CACHE = EstimateCache()


# ---------------------------------------------------------------------------
# 自检（import 时跑一次，保证与 judge_detail 的分值表不漂移）
# ---------------------------------------------------------------------------

def _selftest() -> None:
    """校验本模块的常量与 ``judge_detail`` 一致。

    分值表是两份（正向 / 反向）里最容易分叉的东西，所以在 import 时就钉死 ——
    一旦 ``judge_detail`` 改了分值而这里没跟上，进程直接起不来，而不是
    静默给出与判定细节表矛盾的估分。
    """
    assert SCORE["tap"] == (500, 500, 400, 250, 0), SCORE["tap"]
    assert SCORE["hold"] == (1000, 1000, 800, 500, 0), SCORE["hold"]
    assert SCORE["slide"] == (1500, 1500, 1200, 750, 0), SCORE["slide"]
    assert SCORE["touch"] == (500, 500, 400, 250, 0), SCORE["touch"]
    assert SCORE["break"] == (2500, 2500, 2000, 1000, 0), SCORE["break"]
    assert tuple(BONUS[r] for r in RANKS) == _BONUS
    # judge_detail 的 BREAK_TIERS 里 great1 是 (0.8, 0.40)，本模块按它计
    assert jd.BREAK_TIERS[3] == ("great1", 0.8, 0.40), jd.BREAK_TIERS[3]


_selftest()
