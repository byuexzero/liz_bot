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
指定的连击状态（FC / FC+ / AP / AP+，见 :data:`COMBO_ALLOWED` /
:data:`COMBO_NEED`）。它是**「恰好等于」而不是「不低于」** —— 想要 FC
就必须留一颗 Good，否则会被判成 FC+。

⚠️ **AP / AP+ 的判据在 2026-09-26 订正过**（原先写错了，据此生成的记录在
游戏里显示的连击等级与预期不符）。真源是 ``GameScoreList.cs`` 的
``ComboType``（约 1311 行）::

    if (MissNum == 0 && TheoryCombo != 0)
    {
        if (ach == 101.0 || (breakBonusScore == 0 && ach == 100.0)) → AllPerfectPlus
        else if (TheoryCombo == CriticalNum + PerfectNum)           → AllPerfect
        else if (... + GreatNum)                                    → Gold   (FC+)
        else if (... + GoodNum)                                     → Silver (FC)
    }

**AP+ 的判据是「达成率 == 101.0」，不是「全 CP」。** 达成率 101 只要求
「常规分满分 + break 加成满分」：

* 常规分满分 ⇒ 普通音符是 **CP 还是 P 都行**（两者同分）；
* 加成满分 ⇒ **break 必须是 CP**。

⇒ **AP+ 允许普通音符是小 P，所以它的 DX 分不保证满分。** 这正是「DX 星级」
这一维在 AP+ 下依然有调节余地的原因。

AP 则是「所有音符都是 CP 或 P，且不是 AP+」，于是它等价于
**「至少一颗 break 是小 P」** —— 普通音符的小 P 不改变达成率，也就无法把
AP+ 拉回 AP。所以 :data:`COMBO_NEED` 给 AP 钉的是 **break 的 Perfect**，
不是任意一颗 Perfect。

⚠️ 锁得越死，可达的达成率越稀疏：

* ``AP`` 只允许 CP/P ⇒ **普通音符一颗都不扣分**，达成率只能是
  ``101% - 25k / 加成理论分`` 这一串离散值（k = 降成 P 的 break 数）。
  以 147 为例（break 14 颗）落在 **100.75% ~ 100.98%**，取不到 100.0%。
* ``AP+`` 只允许「普通音符 CP/P + break 全 CP」⇒ 达成率恒为 **101%**。

这不是求解失败，而是数学上就取不到。所以：

* ``AP+`` 的百分比**当约束没有意义**（只有 101% 一个值）⇒ 忽略它，
  直接给这一档（见 :data:`COMBO_PERCENT_IGNORED`）；
* ``AP`` 的百分比**是有意义的**（2026-09-27 起）：在那串离散值里挑用户要的
  那一档，挑不到就报「不可达」（见 :data:`AP_MIN_PERCENT`）——
  以前是默默忽略，用户填了 100.0 却拿到 100.98 会以为算错。

两者都 **DX 星级照常生效** —— 达成率与 DX 分是**两个独立的旋钮**：
达成率由「break 降了几颗」决定，DX 分由「有多少普通音符是小 P」决定。

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

取值落在区间的哪一段（用户口径，2026-09-26）
---------------------------------------------

**刻意不取区间的最大值**（= 下一档星级的门槛 - 1）。取最大值意味着每条记录
都贴着上一档的门槛，看起来就是凑出来的；改成取区间内缩后的一段，见
:data:`STAR_PICK` / :data:`STAR_PICK_MAX` 与 :func:`dx_pick_band`。
推荐段取不到时**退回该星级的完整区间** —— 宁可贴边，也不能给错星级。

**没有「默认给 DX 满分」这回事**：只有显式写 ``dx理论``
（见 :func:`parse_dx_full`）才把 deficit 钉成 0。``AP+`` 也不再自动满分 ——
它只是把「普通音符允许是小 P」打开，DX 落在哪一段由星级参数决定。

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

#: **未指定星级**。与「指定 0★」不同 —— 0★ 是一条真实的约束
#: （DX 落在 ``[1, border(1)-1]``），而「未指定」表示**不约束 DX**：
#: 先按自然落点求出星级，再把 DX 放进那一档的推荐段（见 :func:`dx_pick_band`）。
STAR_ANY = -1

#: 星级区间里的**取值比例** —— 用户口径：不要取区间最大值。
#:
#: 取到区间最大值（= 下一档星级的门槛 - 1）意味着每条记录都贴着上一档的门槛，
#: 一眼就是凑出来的；取区间中段才像正常打出来的成绩。
#: 写成 ``(分子下, 分母下, 分子上, 分母上)`` 的**整数**形式，避免引入浮点。
STAR_PICK = (3, 10, 7, 10)
#: 5★ 的取值比例。5★ 的区间上界**就是 DX 满分**，用 30%-70% 会离满分太远，
#: 反而像刻意压低；靠上一点的 10%-50% 更自然。
STAR_PICK_MAX = (1, 10, 1, 2)

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

#: **推荐段那一趟**的时间上限（秒），比 :data:`TIME_BUDGET` 短得多。
#:
#: 「DX 落在推荐段」只是**美观**要求（别每条记录都贴着上一档的门槛），
#: 而「达成率落进容差带」才是**正确性**要求。两者冲突时正确性优先 ——
#: 所以推荐段这一趟只给一小段预算，取不到就退回完整星级区间再解一次
#: （见 :func:`estimate` 的 ``solve_at``）。
#:
#: ⚠️ 不能省掉这个上限：窄区间让「落带解」稀少，搜索会一直跑到
#: :data:`TIME_BUDGET` 才收尾，既没拿到推荐段、又挤掉了完整区间那一趟的
#: 时间 —— 用户 2026-09-27 实测到的就是这种「两头落空」。
#:
#: 0.8s 是实测拐点（12 谱面 × 72 组 = 864 次，见 ``_tools/bench_estimate.py``）：
#:
#: ============  ==============  =========  ========  =======
#: 本趟预算       推荐段命中率    p99        max       失败
#: ============  ==============  =========  ========  =======
#: 0.4s          81.8%           1824ms     3199ms    0
#: **0.8s**      **82.6%**       **2305ms** **3638ms**  **0**
#: 1.2s          83.1%           2279ms     4134ms    0
#: 3.0s          83.6%           3514ms     5677ms    0
#: ============  ==============  =========  ========  =======
#:
#: 再往上加只多约 1 个百分点的命中率，却把 ``p99`` / ``max`` 抬高三四成 ——
#: 而没命中的那批恰恰就是「星级与百分比离谱」的组合（用户口径：宽松处理）。
STRICT_BUDGET = 0.8


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
#         if (ach == 101.0 || (breakBonusScore == 0 && ach == 100.0)) → AllPerfectPlus
#         else if (TheoryCombo == CriticalNum + PerfectNum)           → AllPerfect
#         else if (TheoryCombo == ... + GreatNum)                     → Gold   (FC+)
#         else if (TheoryCombo == ... + GoodNum)                      → Silver (FC)
#     }
#
# 也就是说：**FC 必须有 Good、FC+ 必须有 Great、AP 必须有 break 小 P**，
# 否则会被判成更高一档。所以「想要 FC」= 禁掉 Miss **且**至少留一颗 Good。
#
# ⚠️ AP+ 的判据是**达成率 == 101.0**（不是「全 CP」），所以它只锁住
# 「普通音符 CP/P + break 全 CP」—— 普通音符的小 P 是允许的。
# 这一点决定了 AP+ 的 DX 分**可以不满分**。详见模块文档。
#
# ⚠️ **连击等级只有 0-4**（``PlayComboFlagID``）—— **没有 -1**。
# 「不限」不是一档等级，用 ``None`` 表示（用户 2026-09-27 明确要求）。
# 所以凡是「等级」的地方一律是 ``int | None``，``None`` = 不加约束。

#: 合法等级。
COMBO_LEVELS = (0, 1, 2, 3, 4)
#: 等级 → **允许**的判定档位，按音符类型分（``"*"`` = 所有类型）。
#:
#: 键必须覆盖 :data:`ZONES` 里的每个类型或落在 ``"*"`` 上 ——
#: :func:`build_items` 按 ``(zone, rank)`` 查这张表。
COMBO_ALLOWED: dict[int, dict[str, tuple[str, ...]]] = {
    0: {},
    1: {"*": ("CriticalPerfect", "Perfect", "Great", "Good")},
    2: {"*": ("CriticalPerfect", "Perfect", "Great")},
    3: {"*": ("CriticalPerfect", "Perfect")},
    # AP+：普通音符 CP/P 都行（同分），但 break 必须全 CP（否则加成不满，
    # 达成率到不了 101% ⇒ 判成 AP）
    4: {"*": ("CriticalPerfect", "Perfect"), "break": ("CriticalPerfect",)},
}
#: 等级 → **必须出现**的判定档位 ``(音符类型或 "*", 档位名)``；``None`` = 无要求。
#:
#: ⚠️ AP（3）钉的是 **break 的 Perfect**，不是任意一颗 Perfect ——
#: 普通音符的小 P 不改变达成率，也就无法把 AP+ 拉回 AP（见模块文档）。
#:
#: ⚠️ 这条要求也顺手覆盖了「**没有 break 的谱面**」：那种谱面上全 CP 就是 AP+，
#: AP 根本判不出来，而这里找不到 break 降级项 ⇒ :func:`solve` 直接报
#: ``combo_unreachable``。**不需要**另写一条「break 数为 0」的特判。
COMBO_NEED: dict[int, tuple[str, str] | None] = {
    0: ("*", "Miss"),
    1: ("*", "Good"),
    2: ("*", "Great"),
    3: ("break", "Perfect"),
    4: None,
}
#: 等级 → 可读名。
COMBO_NAMES: dict[int, str] = {0: "无", 1: "FC", 2: "FC+", 3: "AP", 4: "AP+"}
#: AP 的编号。``x小`` 写法蕴含它，所以要有个名字而不是散落的字面量 3。
COMBO_AP = 3
#: AP+ 的编号。
COMBO_AP_PLUS = 4
#: **会忽略百分比**的等级 —— 现在**只剩 AP+**。
#:
#: AP+ 只允许「普通音符 CP/P + break 全 CP」⇒ 加成必然满分 ⇒ 达成率恒为
#: **101%**，用户给什么百分比都取不到，所以干脆不当约束：直接给这一档。
#:
#: ⚠️ **AP（3）从 2026-09-27 起不再忽略百分比** —— 用户要求「百分比除了
#: AP+ 等级外是必要的」。AP 的达成率虽然也是一串离散值，但**可以按用户给的
#: 百分比挑**（见 :data:`AP_MIN_PERCENT`），挑不到就报错，而不是默默忽略。
#:
#: ⚠️ 这两档都**只忽略百分比，星级照常生效**（2026-09-26 订正）。
#: 达成率与 DX 分是**两个独立的旋钮**：达成率由「break 降了几颗」决定，
#: DX 分由「有多少普通音符是小 P」决定。
COMBO_PERCENT_IGNORED: tuple[int, ...] = (COMBO_AP_PLUS,)

#: AP 的达成率**不可能**低于这个数（单位 1/10000 %）—— 低于它直接报「不可达」。
#:
#: 推导（与谱面无关，任何谱面都成立）：AP 只允许 CP/P，于是
#:
#: * 常规分必然满分（普通音符 CP 与 P 同分），扣分**只**来自 break 的加成；
#: * AP 要求「至少一颗 break 是小 P」（否则加成满分 ⇒ 判成 AP+），
#:   而 break 的小 P 每颗扣 25 点加成；
#: * 加成理论分 = ``100 × break 数``，所以最大扣分 = ``25 × break 数 /
#:   (100 × break 数) = 0.25`` 个百分点。
#:
#: ⇒ **AP 的达成率恒在 ``[100.75%, 101%)`` 之内**。取 100.5 当门槛是留了余量
#: 的**与谱面无关**的下界（用户 2026-09-27 口径：「用户输出 100.5 以下直接
#: 提示不可达」）。
AP_MIN_PERCENT = 1005000

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


def star_span(borders: list[int], full: int, star: int) -> tuple[int, int]:
    """某星级在 **DX 分** 上的闭区间 ``(下界, 上界)``。

    上界是「再高一档的门槛 - 1」；最高星没有下一档，上界就是 DX 满分。
    """
    return borders[star], (borders[star + 1] - 1 if star < MAX_STAR else full)


def dx_pick_band(borders: list[int], full: int, star: int) -> tuple[int, int]:
    """该星级**推荐取值的 DX 区间** —— 在 :func:`star_span` 上按比例内缩。

    这就是「不要输出区间最大值」的落点：返回值永远在星级内部、不贴边
    （见 :data:`STAR_PICK` / :data:`STAR_PICK_MAX`）。
    """
    lo, hi = star_span(borders, full, star)
    num_lo, den_lo, num_hi, den_hi = (
        STAR_PICK_MAX if star == MAX_STAR else STAR_PICK)
    span = hi - lo
    return lo + span * num_lo // den_lo, lo + span * num_hi // den_hi


def dx_band(
    borders: list[int], full: int, star: int,
    *, dx_full: bool = False, strict: bool = True,
) -> tuple[int, int, int]:
    """把「星级 / ``dx理论``」翻译成 **deficit** 的 ``(允许区间, 补位目标)``。

    deficit = DX 满分 - DX 分，所以区间上下界要**反过来**。

    * ``dx_full`` ⇒ ``(0, 0, 0)`` —— DX 必须满分；
    * ``star == STAR_ANY`` ⇒ ``(0, full, 0)`` —— 不约束 DX，也不补位；
    * 否则取该星级的推荐段（``strict=False`` 时取完整区间），补位目标
      **取推荐段的中点**：贴边（哪怕是推荐段的边）看起来一样像凑出来的。
      退回完整区间时目标改成**离推荐段最近**的那一端，免得白跑更远。
    """
    if dx_full:
        return 0, 0, 0
    if star == STAR_ANY:
        return 0, full, 0

    pick_lo, pick_hi = dx_pick_band(borders, full, star)
    if strict:
        dx_lo, dx_hi = pick_lo, pick_hi
        prefer_dx = (pick_lo + pick_hi) // 2
    else:
        dx_lo, dx_hi = star_span(borders, full, star)
        prefer_dx = pick_lo
    return full - dx_hi, full - dx_lo, full - prefer_dx


def combo_status(counts: dict[str, int]) -> int:
    """连击状态（``PlayComboFlagID``）：0 无 / 1 FC / 2 FC+ / 3 AP / 4 AP+。

    与 ``GameScoreList.ComboType`` 逐条对齐 —— ⚠️ **AP+ 的判据是
    「达成率 == 101.0」，不是「全 CP」**：

    * 达成率 101 ⇔ 常规分满分 **且** break 加成满分；
    * 常规分满分 ⇔ 所有音符都是 CP 或 P（Great 起就掉分）；
    * 加成满分 ⇔ 所有 break 都是 CP。

    所以「没有 Miss/Good/Great + break 全 CP」就是 AP+，**普通音符可以是小 P**。
    剩下「没有 Miss/Good/Great 但 break 里有小 P」的才是 AP。

    没有 break 的谱面（``breakBonusScore == 0``）走游戏里那条
    ``breakBonusScore == 0 && ach == 100.0`` 的支路 —— 等价于这里的
    「break 颗数为 0 ⇒ 视为加成满分」，所以 AP 在那类谱面上不可能出现。
    """
    total = sum(counts[f"{z}{r}"] for z in ZONES for r in RANKS)
    if not total:
        return 0
    if sum(counts[f"{z}Miss"] for z in ZONES):
        return 0                      # 无
    if sum(counts[f"{z}Good"] for z in ZONES):
        return 1                      # FullCombo
    if sum(counts[f"{z}Great"] for z in ZONES):
        return 2                      # FullComboPlus
    if counts["breakPerfect"]:
        return 3                      # AllPerfect
    return 4                          # AllPerfectPlus（含无 break 的谱面）


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
    normal: int, bonus: int, *, allowed: dict[str, tuple[str, ...]] | None = None,
) -> list[tuple]:
    """列出全部降级项，**并按预算裁剪每项的可用数量**。

    每项是 ``(zone, rank, scoreLoss, bonusLoss, dxLoss, limit, normal, bonus)``。
    裁剪上限不是可有可无的优化：二分拆包的数量随 ``limit`` 取对数增长，
    不裁剪会让背包的包数翻倍、DP 直接慢一倍。

    :param allowed: 「音符类型 → 允许的判定档位」白名单，见
        :data:`COMBO_ALLOWED`（``"*"`` 是通配键）。``None`` / 空字典 = 全允许。
        不在白名单里的档位**根本不会被列成降级项**，于是求解器用不到它 ——
        combo 等级就是靠这里落地的：想要 FC+ 就不能用 Good / Miss 来凑分。
    """
    items: list[tuple] = []
    allowed = allowed or {}

    def permitted(zone: str, rank: str) -> bool:
        """该音符类型下这个判定档位是否可用（白名单缺省 = 全允许）。"""
        ranks = allowed.get(zone, allowed.get("*"))
        return ranks is None or rank in ranks

    def effective(limit: int, value: int, bl: int, weight: int) -> int:
        if value:
            limit = min(limit, l_max // value)
        if bl:
            limit = min(limit, b_max // bl)
        return min(limit, hi // weight)

    for z in NORMAL_ZONES:
        n = zone_counts[z]
        if not n:
            continue
        for i in (2, 3, 4):                      # Great / Good / Miss
            if not permitted(z, RANKS[i]):
                continue
            value = SCORE[z][0] - SCORE[z][i]
            weight = 2 if i == 2 else 3
            items.append((z, RANKS[i], value, 0, weight,
                          effective(n, value, 0, weight), normal, bonus))
    if zone_counts["break"]:
        n = zone_counts["break"]
        for i, weight in ((1, 1), (2, 2), (3, 3), (4, 3)):   # P / Great / Good / Miss
            if not permitted("break", RANKS[i]):
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
    combo: int | None = None, *, dx_full: bool = False, strict: bool = True,
    budget: float | None = None,
) -> tuple[Solution | None, str | None]:
    """求「达成率 ≥ ``target`` 且 DX 落在指定星级区间」的最小可达达成率。

    :param target: 目标达成率，单位 1/10000 %（``1000000`` = 100.0%）
    :param want_star: 目标星级 0-5，或 :data:`STAR_ANY`（**不约束 DX**）
    :param combo: 连击等级 ``0``-``4``，或 ``None`` = **不限**。
        **是「恰好等于」而不是「不低于」** —— 想要 FC 就必须留一颗 Good，
        想要 AP 就必须留一颗 break 小 P，否则会被判成更高一档。
        见 :data:`COMBO_ALLOWED` / :data:`COMBO_NEED`。
        ⚠️ **没有 -1 这一档**：不限不是等级，是「没有等级」（``None``）。
    :param dx_full: ``True`` 时把 deficit 钉成 0 —— DX 拿满分（``dx理论``）。
    :param strict: ``True`` = DX 必须落进 :func:`dx_pick_band` 的**推荐段**
        （区间的 30%-70%，5★ 是 10%-50%）；``False`` = 退回该星级的**完整**
        区间。推荐段取不到时由调用方拿 ``False`` 再解一次。
    :param budget: 本趟的时间上限（秒）；``None`` = :data:`TIME_BUDGET`。
        推荐段那一趟要传 :data:`STRICT_BUDGET` —— 它只是美观要求，不该
        挤掉完整区间那一趟的预算。
    :returns: ``(解, 错误文案键)``。解为 ``None`` 时第二个元素是失败原因。

    做法：把「D ≤ Dmax 且 deficit 落进 ``[band_lo, band_hi]`` 下最大化 D」写成
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

    allowed = COMBO_ALLOWED.get(combo, {})
    need = COMBO_NEED.get(combo)

    borders, full = star_borders(total_notes)
    band_lo, band_hi, prefer = dx_band(
        borders, full, want_star, dx_full=dx_full, strict=strict)
    # 该区间为空只可能是「满星时下一档门槛超过满分」—— 由 STAR_ACHIEVE 的取值
    # 与 total_notes > 0 可证不可能发生。留着是**防御**：真发生了也走同一个
    # 文案，不另立一个永远显示不出来的键。
    if band_lo > band_hi:
        return None, "estimate.no_solution"

    d_num = ACH_MAX - target                     # Dmax，单位 1/10000 %
    if d_num < 0:
        return None, "estimate.too_high"

    l_max = d_num * normal // 1000000
    b_max = min(d_num * bonus // 10000, 100 * zone_counts["break"])
    items = build_items(zone_counts, l_max, b_max, band_hi, normal, bonus,
                        allowed=allowed)

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

    def band_ok(deficit: int, zone_used: tuple[int, ...]) -> bool:
        """免费补位之后，deficit 能不能落进 ``[band_lo, band_hi]``。

        补位只会**抬高** deficit（CP→P 每颗 +1 DX 亏损），所以可达的 deficit
        是一段区间 ``[deficit, deficit + 空闲补位容量]``；只要它与目标区间相交
        就算合格，具体落在哪一点由 :func:`_pad_to_band` 收尾时决定。
        """
        if deficit > band_hi:
            return False
        free = pad_notes - sum(zone_used[i] for i in pad_idx)
        return deficit + free >= band_lo

    zone_index = {z: i for i, z in enumerate(ZONES)}

    # dp[(L, B)] = (最少 deficit, 各降级项用量, 各音符类型已降级数)
    #
    # **combo 等级「必须出现」的档位靠起点钉住**：不空手起手，而是预先吃进
    # 一颗该档位的降级。这样「≥ 1 颗」就等价于「所有可达状态都满足」，
    # 不必给 DP 再加一维 —— 加一维会让状态数与内存直接翻倍，而这里只多几个起点。
    #
    # ⚠️ AP（``("break", "Perfect")``）钉的是 **break 的小 P**：那 25 点加成损失
    # 是**强制**的（不然达成率就是 101% ⇒ 判成 AP+），所以它必须走降级项；
    # 而普通音符的小 P 走免费补位，一颗都不该预吃。
    dp: dict[tuple[int, int], tuple[int, tuple[int, ...], tuple[int, ...]]] = {}
    if need is None:
        dp[(0, 0)] = (0, (0,) * len(items), (0,) * len(ZONES))
    else:
        need_zone, need_rank = need
        for idx, item in enumerate(items):
            if item[1] != need_rank:
                continue
            if need_zone != "*" and item[0] != need_zone:
                continue
            value, bl, weight = item[2], item[3], item[4]
            if value > l_max or bl > b_max or weight > band_hi:
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
    #: 本趟的时间上限 —— 推荐段那一趟只给 :data:`STRICT_BUDGET`（见 ``budget``）。
    budget_s = TIME_BUDGET if budget is None else budget
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
            if new_l > l_max or new_b > b_max or new_w > band_hi:
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
        if len(dp) > MAX_STATES or time.monotonic() - started > budget_s:
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
        return None, ("estimate.combo_unreachable" if combo is not None
                      else "estimate.no_solution")

    _obj, _l, _b, deficit, uses = best
    counts = {f"{z}{r}": 0 for z in ZONES for r in RANKS}
    for idx, item in enumerate(items):
        counts[f"{item[0]}{item[1]}"] += uses[idx]
    for z in ZONES:
        used = sum(counts[f"{z}{r}"] for r in RANKS[1:])
        counts[f"{z}CriticalPerfect"] = zone_counts[z] - used

    # CP → P 不改变达成率（同分），但每颗 -1 DX —— 这就是把 deficit 抬进
    # 目标区间的**免费旋钮**，补到 ``prefer``（推荐段中点）为止。
    # ⚠️ 只对**非 break** 这么做：break 的 CP 加成 100 / P 只有 75，
    # 转换会真的扣掉达成率（见 judge_detail.BREAK_TIERS）。
    counts, _padded = _pad_to_band(counts, full, prefer=prefer, ceiling=band_hi)

    dx = deluxscore(counts)
    stars = stars_of(dx, borders)
    achievement = achievement_int(counts, normal, bonus)

    # 安全网三条：区间没落进去 / 星级对不上 / 连击状态对不上，一律报「取不到解」。
    # 宁可什么都不给，也不能给用户一份「判定与连击状态对不上」或「星级标错」
    # 的记录 —— 那是要拿去上传的。
    if not (band_lo <= full - dx <= band_hi):
        return None, "estimate.no_solution"
    if want_star != STAR_ANY and stars != want_star:
        return None, "estimate.no_solution"
    if combo is not None and combo_status(counts) != combo:
        return None, "estimate.combo_unreachable"

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
    counts: dict[str, int], full: int, *, prefer: int, ceiling: int,
) -> tuple[dict[str, int], int]:
    """把 CP 降成 P，把 deficit **尽量抬到** ``prefer``（上限 ``ceiling``）。

    补位是**免费**的：CP 与 P 在普通音符上同分，所以达成率一点不变，
    只有 DX 分每颗 -1。于是它成了「在不影响达成率的前提下微调 DX」的唯一旋钮。

    :param prefer: 想达到的 deficit（一般是目标区间的下界）。补不到就算了 ——
        可用容量不够时**补多少算多少**，剩下的由调用方按 ``ceiling`` 判定成败。
    :param ceiling: deficit 的上限（目标区间的上界），补到它就必须停。
    :returns: ``(新的判定明细, 实际补了几颗)``。

    ⚠️ **只动 :data:`PAD_ZONES`（tap / hold / touch），两个都不能碰：**

    * **break** —— CP 加成 100、P 只有 75（``judge_detail.BREAK_TIERS``），
      降成 P 会真的扣达成率，那就不再是「免费补位」，而是一个要进背包
      一起权衡的取舍；
    * **slide** —— 它根本没有小 P（``judgeParamTbl[2]`` 的 PERFECT 区间是
      退化的空区间，见 :data:`PAD_ZONES`）。降出来的是游戏里打不出的判定。
    """
    deficit = full - deluxscore(counts)
    need = max(0, min(prefer, ceiling) - deficit)
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
    *, want_star: int = STAR_ANY, dx_full: bool = False, strict: bool = True,
) -> Solution | None:
    """``x小`` 的构造：break 恰好 ``count`` 颗小 P，其余全 CP。

    ``x小`` 是**显式指定**的一档 —— 它把「break 降了几颗」写死，于是
    **达成率与连击等级随之确定**：

    * ``count == 0`` ⇒ 加成满分 + 常规分满分 ⇒ 达成率 **101%** ⇒ 游戏判
      **AP+**（⚠️ **不是 AP**：全 CP 会先命中 ``ach == 101.0`` 那条分支，
      见模块文档）；
    * ``count > 0`` ⇒ 达成率 ``101% - 25·count / 加成理论分`` ⇒ 判 **AP**。

    DX 分则另有旋钮：**普通音符的 CP→P 不改变达成率**（同分），所以给了
    ``want_star`` 就按该星级的推荐段补位，没给就保持全 CP。

    ⚠️ **普通小 P 只放在 :data:`PAD_ZONES`（tap / hold / touch）**：
    slide 根本没有小 P（``judgeParamTbl[2]`` 的 PERFECT 区间是退化的空区间）。

    :param count: break 的小 P 数，取值 ``0 .. zone_counts["break"]``
    :param want_star: 目标星级；:data:`STAR_ANY` = 不调 DX（全 CP）
    :param dx_full: ``True`` = 要求 DX 满分。与 ``count > 0`` 矛盾 ⇒ 返回 ``None``
    :returns: ``Solution``；``count`` 超范围 / 需要的普通小 P 放不下时 ``None``
    """
    if count < 0 or count > zone_counts["break"]:
        return None

    counts = {f"{z}{r}": 0 for z in ZONES for r in RANKS}
    for z in ZONES:
        counts[f"{z}CriticalPerfect"] = zone_counts[z]
    if count:
        counts["breakCriticalPerfect"] -= count
        counts["breakPerfect"] += count

    if dx_full:
        # 只有「break 一颗都没降」才可能拿满分，否则直接判失败
        if count:
            return None
        band_lo, band_hi, prefer = 0, 0, 0
    else:
        band_lo, band_hi, prefer = dx_band(
            borders, full, want_star, dx_full=False, strict=strict)

    if want_star != STAR_ANY or dx_full:         # 有 DX 约束才需要补位
        counts, _ = _pad_to_band(counts, full, prefer=prefer, ceiling=band_hi)
        if not (band_lo <= full - deluxscore(counts) <= band_hi):
            return None                          # 普通小 P 不够补到目标区间

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
    """**实际生效**的星级 0-5。

    用户写了就是它；没写（见 ``star_auto``）时是算法按自然落点定下来的那一档 ——
    :attr:`full_star_span` 与 :attr:`pick_band` 都靠它，所以这里必须是**生效值**
    而不是 :data:`STAR_ANY`。只有连探针都失败时才会是 :data:`STAR_ANY`
    （不约束 DX）。
    """
    star_auto: bool
    """``want_stars`` 是不是**算法自己定**的（用户没写星级时先按自然落点求一次）。"""
    dx_full: bool
    """是不是显式要求 DX 满分（``dx理论``）。"""
    pick_band: tuple[int, int] | None
    """本次实际使用的**推荐 DX 区间** ``(下界, 上界)``；``None`` = 没套区间。
    结果落在区间外说明推荐段取不到、退回了完整星级区间（见 :func:`dx_pick_band`）。"""
    combo: int | None
    """要求的 combo 等级（``0``-``4``），或 ``None`` = **不限**。
    ⚠️ **没有 -1 这一档** —— 不限不是等级，是「没有等级」。"""
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

    @property
    def full_star_span(self) -> tuple[int, int] | None:
        """``want_stars`` 那一档的**完整** DX 区间；星级不限时 ``None``。

        用来在「推荐段取不到、退回了完整区间」时告诉用户实际落在哪一段
        （见 :func:`dx_pick_band` / ``estimate.dx_out``）。
        """
        if self.want_stars == STAR_ANY:
            return None
        return star_span(self.borders, self.max_deluxscore, self.want_stars)

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
    """解析星级写法 → 0-5（或 :data:`STAR_ANY`）。收 ``2`` / ``2星`` / ``★★``。

    空串返回 :data:`STAR_ANY`（**不限**）—— 与「指定 0★」是两件事：
    0★ 是一条真实约束（DX 落进 ``[1, border(1)-1]``），而不限表示不碰 DX。
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return STAR_ANY
    if set(raw) == {"★"}:
        return len(raw) if 1 <= len(raw) <= MAX_STAR else None
    raw = raw.rstrip("星").strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if 0 <= value <= MAX_STAR else None


#: ``dx理论`` / ``dx满分`` 等写法。大小写不敏感，允许 ``dx`` 与中文之间空格。
_DX_FULL_RE = re.compile(r"^dx\s*(理论分?|满分|max)$", re.IGNORECASE)


def parse_dx_full(text: str) -> bool:
    """是不是显式要求 **DX 满分**（``dx理论`` / ``dx满分`` / ``dxmax``）。

    这是**唯一**能把 deficit 钉成 0 的写法 —— 其余情况下算法一律按星级的
    推荐段取 DX，绝不会「顺手给满分」（见模块文档的「取值落在区间的哪一段」）。
    """
    return bool(isinstance(text, str) and _DX_FULL_RE.match(text.strip()))


def parse_combo(text: str) -> int | None:
    """解析 combo 等级 → ``0``-``4``。收 ``0``-``4`` / ``FC`` / ``FC+`` / ``AP`` / ``AP+``。

    大小写不敏感，也收全角 ``＋``。

    ⚠️ **空串与不认识都返回 ``None``** —— 因为「不限」不是一档等级
    （**没有 -1**），它和「看不懂」的区别由调用方按 ``text.strip()`` 判：
    空 = 用户没写 = 不限；非空而解析不出来 = 看不懂（见 :func:`estimate`）。
    要「只认认得出来的写法」用 :func:`is_combo`。
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    return COMBO_ALIASES.get(raw.lower())


def is_combo(text: str) -> bool:
    """是不是一个**认得出来**的连击等级写法（``FC`` / ``AP+`` / ``3`` …）。

    ``""`` 与乱写的串都返回 ``False`` —— 参数整理层用它判断某个 token
    该不该落进 combo 那一格（见 ``command_router._estimate_args``）。
    """
    return parse_combo(text) is not None


#: ``x小`` / ``x小P`` 写法。``小`` 后面可跟一个 ``p`` / ``P``。
_BREAK_P_RE = re.compile(r"^(\d+)\s*小\s*[pP]?$")


def parse_break_p(text: str) -> int | None:
    """解析 ``x小`` / ``x小P`` → **break 的 P 判定数**。不认识返回 ``None``。

    这是 **AP / AP+ 专用的另一种写法**（用户口径：``x小`` = 「AP 时 break 的 P
    判定数」）。这两档下普通音符一颗都不扣分，所以 ``x`` 一写下去，
    **达成率与连击等级就都确定了**（``0小`` ⇒ 加成满分 ⇒ 达成率 101% ⇒ AP+；
    ``x>0`` ⇒ AP），见 :func:`build_break_p_solution`。DX 分另有旋钮
    （普通音符的小 P 数），所以星级参数仍然可用。

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

    :param percent: 目标达成率。**除 ``AP+`` 外必填**（用户 2026-09-27 口径）——
        ``x小`` / ``dx理论`` 自带达成率，也不用给；只有 ``AP+`` 是「给什么
        都取不到」，所以直接忽略。
    :param stars: 星级 —— ``3`` / ``3星`` / ``★★★``，或 ``dx理论``（DX 满分）。
        空串 = **不限**（DX 不受约束，按自然落点的星级再取推荐段）。
    :param combo: combo 等级 ``0``-``4``（也收 ``FC`` / ``FC+`` / ``AP`` / ``AP+``）；
        空串 = **不限**。⚠️ **没有 -1 这一档** —— 不限不是等级。
    :param break_p: ``x小`` / ``x小P`` 写法 —— **break 的小 P 数**。
        给了它，达成率与连击等级就随之确定（见 :func:`build_break_p_solution`），
        百分比不再需要；星级仍可选，用来调 DX 分。
    :returns: ``(Estimate, None)`` 或 ``(None, 文案)``。
        **失败时返回的是已经渲染好的用户可见文案**（不是键）—— 这样调用方
        （:func:`estimate_reply` / :mod:`liz_bot.estimate_image` /
        ``command_router._is_failure``）都不必再知道每个键要什么占位符。
        ``judge.no_chart`` 的占位符要列可用难度，只有这里拿得到，也只能在这里渲染。

    ⚠️ **AP / AP+ 都只忽略百分比，不忽略星级**（见 :data:`COMBO_PERCENT_IGNORED`）。
    这两档把可用判定档位锁死之后，普通音符一颗都不扣分，只有 break 降成 P 才扣，
    粒度是 ``25 / 加成理论分`` —— 以 147 为例，AP 只能是
    ``101% - 25k/1400``（``k`` = break 的 P 数），落在 100.75% ~ 100.98%。

    * ``AP+`` 只有 101% 一个值 ⇒ 百分比**没有意义**，忽略它（用户口径：
      「AP+ 直接输出回复忽略达成率」）。
    * ``AP`` **要用**百分比：在那串离散值里挑用户要的那一档。低于
      :data:`AP_MIN_PERCENT`（100.5%）直接报「不可达」，而不是默默给 100.98%
      —— 用户填 100.0 却拿到 100.98 会以为算错。

    两者都 **DX 分是另一个旋钮**（「有多少普通音符是小 P」），星级照常生效。
    """
    index = jd.resolve_difficulty(difficulty)
    if index is None:
        return None, replies.text("judge.bad_difficulty", value=difficulty)

    # combo 先解析：它决定了百分比还要不要
    # ⚠️ 「不限」与「看不懂」都解析成 None —— 靠**串本身非空**来区分
    # （不限不是一档等级，见 parse_combo）
    if combo.strip():
        want_combo = parse_combo(combo)
        if want_combo is None:
            return None, replies.text("estimate.bad_combo", value=combo)
    else:
        want_combo = None                          # 不限

    want_break_p = parse_break_p(break_p) if break_p.strip() else None
    if break_p.strip() and want_break_p is None:
        return None, replies.text("estimate.bad_break_p", value=break_p)
    if want_break_p is not None and want_combo not in (None, COMBO_AP):
        # ``x小`` 就是 AP 的写法，与别的 combo 等级互斥
        return None, replies.text("estimate.combo_conflict",
                                  combo=COMBO_NAMES.get(want_combo, combo))

    want_dx_full = parse_dx_full(stars)
    if want_dx_full:
        if want_break_p is not None:
            # ``x小`` 已经把 DX 分钉在「满分 - x」上了，两者不可能同时成立
            return None, replies.text("estimate.dx_conflict")
        want_star = MAX_STAR                       # DX 满分必然是 5★
    else:
        want_star = parse_stars(stars)
        if want_star is None:
            return None, replies.text("estimate.bad_stars", value=stars)

    # 百分比的**语法**先校验（「看不懂的百分比」比「查不到歌」更值得先说）。
    # 数值的语义校验（AP 的下界）也放这里 —— 那条界与谱面无关（见 AP_MIN_PERCENT）。
    percent_value = None
    if percent.strip():
        percent_value = parse_percent(percent)
        if percent_value is None or percent_value < ACH_MIN:
            return None, replies.text("estimate.bad_percent", value=percent)
        if percent_value > ACH_MAX:
            # 写法合法但超出理论上限（101%）—— 与「看不懂」分开，提示才有用。
            return None, replies.text("estimate.too_high")
        if want_combo == COMBO_AP and percent_value < AP_MIN_PERCENT:
            # AP 的达成率有**下界**（恒 ≥ 100.75%，见 AP_MIN_PERCENT）——
            # 低于门槛直接说不可达，别默默给 100.98% 让用户以为算错。
            return None, replies.text("estimate.ap_unreachable", value=percent)
    elif (want_break_p is None and not want_dx_full
          and want_combo != COMBO_AP_PLUS):
        # 2026-09-27 起**只有 AP+ 可以不填百分比**（它给什么都取不到）。
        return None, replies.text("estimate.need_percent")

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

    # ---- 定 target ----
    if want_break_p is not None:
        target = 0                                 # 由构造直接得出，随后回填
    elif want_dx_full:
        # ``dx理论`` ⇒ 全 CP ⇒ 达成率必然是 101%，百分比给什么都取不到
        target = ACH_MAX
    elif want_combo == COMBO_AP_PLUS:
        # AP+ 只有 101% 一个值 ⇒ 百分比没有意义，忽略它（用户口径：
        # 「AP+ 直接输出回复忽略达成率」）。
        target = ACH_MAX
    else:
        # ⚠️ **AP 走这里**（2026-09-27 起）：在那串离散值里挑用户要的那一档。
        # 以前是直接顶替成「AP 能达到的最高达成率」，等于默默忽略百分比 ——
        # 用户填 100.0 却拿到 100.98% 会以为算错。
        target = percent_value

    def pick_band(star: int) -> tuple[int, int] | None:
        """该星级实际要用的推荐 DX 区间；星级不限 / DX 满分时为 ``None``。"""
        if want_dx_full or star == STAR_ANY:
            return None
        return dx_pick_band(borders, full, star)

    def wrap(solution: Solution, target_: int, star_: int, star_auto_: bool) -> Estimate:
        """两条路径（``x小`` / 背包搜索）共用的结果装配。

        ``star_`` 为 :data:`STAR_ANY` 时一律算「用户没指定」—— 显示成「不限」，
        并且没有星级区间可谈（``pick_band`` / ``full_star_span`` 都给 ``None``）。
        """
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
            star_auto=star_auto_ or star_ == STAR_ANY,
            dx_full=want_dx_full,
            pick_band=pick_band(star_),
            combo=want_combo,
            break_p=want_break_p,
            borders=borders,
            max_deluxscore=full,
        )

    # ---- 两条求解路径统一成 ``solve_at(star)`` ----
    # 返回 ``(解, 错误键)``：**两趟**——先按该星级的推荐段（30%-70%，5★ 是
    # 10%-50%），取不到再退回该星级的**完整**区间。宁可 DX 贴边，也不能给错星级。
    if want_break_p is not None:
        def solve_at(star_: int) -> tuple[Solution | None, str | None]:
            """``x小``：break 恰好这么些小 P ⇒ 达成率与连击等级随之确定。"""
            for strict in (True, False):
                got = build_break_p_solution(
                    zone_counts, want_break_p, normal, bonus, borders, full,
                    want_star=star_, dx_full=want_dx_full, strict=strict)
                if got is not None:
                    return got, None
            return None, "estimate.bad_break_p"
    else:
        def solve_at(star_: int) -> tuple[Solution | None, str | None]:
            """背包搜索：先按该星级的**推荐段**解，取不到就退回**完整区间**。

            回退的判据是「**没落进容差带**」，不只是「无解」—— 这是
            2026-09-27 修的一处：窄区间让落带解稀少，推荐段那一趟会一直跑到
            时间上限才收尾，交出一个 ``gap`` 超容差的结果，而完整区间那一趟
            本来又快又能落带（见 :data:`STRICT_BUDGET`）。

            ⚠️ 退回完整区间意味着 DX 会**贴到该星级的边上**（推荐段本来就是
            为「别每条都贴着上一档门槛」而存在的）。这是有意的取舍：**正确性
            优先于美观** —— 用户口径「5★ 通常都在 100.9% 以上，这种离谱组合
            宽松处理」。贴边由 ``estimate.dx_out`` 如实说明。
            """
            got, err = solve(zone_counts, target, star_, want_combo,
                             dx_full=want_dx_full, budget=STRICT_BUDGET)
            if not want_dx_full and star_ != STAR_ANY and (
                    got is None or not got.within_tolerance):
                alt, alt_err = solve(zone_counts, target, star_, want_combo,
                                     dx_full=want_dx_full, strict=False)
                # 留更贴近目标的那个（``gap`` 小 = 达成率更接近用户要的数）
                if alt is not None and (got is None or alt.gap < got.gap):
                    got, err = alt, alt_err
            return got, err

    # ---- 星级不限时先探一次自然落点 ----
    # 用户没写星级 ⇒ 不能凭空替他选一档，更不能「顺手给满分」（用户口径：
    # 除了显式 ``dx理论``，DX 一律落在推荐段里）。先按不约束 DX 解一次，
    # 拿结果的星级，再按那一档的推荐段重解。
    star_eff, star_auto = want_star, False
    if want_star == STAR_ANY and not want_dx_full:
        probe, _err = solve_at(STAR_ANY)
        if probe is not None:
            star_eff, star_auto = probe.stars, True

    solution, err = solve_at(star_eff)
    if solution is None and star_auto:
        # 该星级的推荐段与完整区间都取不到（普通小 P 不够补位 / 物量太少）
        # ⇒ 退回自然落点。这时 DX 会高于推荐段，由 ``estimate.dx_out`` 说明。
        solution, err = solve_at(STAR_ANY)
    if solution is None:
        # 失败键 → 渲染。``_FAILURE_KEYS`` 里的键都要带对占位符，
        # 缺占位符 ``replies.text`` 会直接抛错（刻意不做静默回退）。
        if err == "estimate.combo_unreachable":
            return None, replies.text("estimate.combo_unreachable",
                                      combo=COMBO_NAMES.get(want_combo, str(want_combo)))
        if err == "estimate.bad_break_p":
            return None, replies.text("estimate.bad_break_p", value=break_p)
        return None, replies.text(err or "estimate.no_solution")

    # ``x小`` 的达成率由构造得出，target 回填成它 ⇒ gap == 0、within_tolerance 为真
    final_target = solution.achievement if want_break_p is not None else target
    return wrap(solution, final_target, star_eff, star_auto), None


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
    # ``None`` = 用户没要求（不限）；没有 -1 这一档，所以判 None 而不是判负数
    want = "不限" if est.combo is None else COMBO_NAMES.get(est.combo, "")
    return want, label


def stars_summary(est: Estimate) -> tuple[str, str | None]:
    """``estimate.stars`` 的 ``{want}`` 占位符 + 可选的补充说明行。

    ``want`` 有三种形态：写死的星级、``不限``（用户没写，由算法按自然落点取）、
    ``dx理论``。

    补充说明**只在推荐段没取到时**出现 —— 那时 DX 会贴到该星级的边上
    （见 :func:`dx_pick_band` 的「取不到就退回完整区间」），不说明白用户会
    以为算错了。
    """
    if est.dx_full:
        want = replies.text("estimate.want_dx_full")
    elif est.star_auto:
        want = replies.text("estimate.want_any")
    else:
        want = replies.text("estimate.want_star", star=est.want_stars)

    note = None
    if est.pick_band is not None:
        plo, phi = est.pick_band
        if not (plo <= est.solution.deluxscore <= phi):
            span = est.full_star_span
            note = replies.text(
                "estimate.dx_out",
                stars=est.solution.stars,
                lo=span[0] if span else plo, hi=span[1] if span else phi,
                plo=plo, phi=phi,
            )
    return want, note


def ignored_note(est: Estimate) -> str | None:
    """百分比被忽略时的说明行；没忽略则 ``None``。

    结果行里的「目标」不是用户给的数，而是算法自己取的该条件下上限。
    不说明白，用户会以为那就是他要的百分比。三种情况各有一句：

    * ``x小`` —— 达成率与连击等级**都由 break 的小 P 数推出来**；
    * ``dx理论`` —— DX 满分 ⇒ 全 CP ⇒ 达成率必然是 101%；
    * **``AP+``** —— 可用档位被锁死，达成率只剩 101% 一个值。

    ⚠️ **``AP`` 不在这个名单里**（2026-09-27 起）：它**要用**用户给的百分比，
    所以「目标」就是用户填的那个数，没什么要说明的（见
    :data:`COMBO_PERCENT_IGNORED`）。
    """
    if est.break_p is not None:
        return replies.text("estimate.break_p_note", count=est.break_p)
    if est.dx_full:
        return replies.text("estimate.dx_full_note")
    if est.combo not in COMBO_PERCENT_IGNORED:
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
    """``/估分 <歌曲id> <难度> <百分比> [dx星级] [combo等级]`` 的回复。

    成功时把结果**存进一轮缓存**（见 :data:`CACHE`）—— 目前只存不取，
    留着接口等产品决定下一轮拿它做什么。

    :param stars: 省略时**不约束 DX** —— 先按自然落点求出星级，再取那一档的
        推荐段（见 :func:`dx_pick_band`）；写 ``dx理论`` 则要求 DX 满分。
    :param combo: 省略时按「**不限**」（``None`` —— 没有 -1 这一档）。
    :param break_p: ``x小`` / ``x小P`` 写法（break 的小 P 数）。
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
    # combo 等级把可用档位卡死之后（如 AP 只允许 CP/P，普通音符一颗都不扣），
    # 或者 DX 被要求落进某一段、逼得算法只能少扣分。
    # 用户要的是能直接上传的记录，不告警他会以为这就是他要的百分比。
    gap_warn = (
        replies.text("estimate.gap_warn", gap=_pct(sol.gap))
        if sol.gap > TOLERANCE else None
    )
    combo_want, combo_actual = combo_summary(est)
    star_want, star_note = stars_summary(est)
    return "\n".join(filter(None, [
        header,
        replies.text("estimate.result",
                     target=_pct(est.target), actual=_pct(sol.achievement)),
        replies.text("estimate.stars", stars=sol.stars, want=star_want,
                     dx=sol.deluxscore, max_dx=est.max_deluxscore),
        star_note,
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
