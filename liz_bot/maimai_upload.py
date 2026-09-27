"""把「传分」接到 liz_bot —— ``#上传`` 的实现。

成绩从哪来（**缓存优先**）
==========================
1. **``/估分`` 的会话缓存**（:data:`liz_bot.score_estimate.CACHE`）。
   ``Estimate.payload()`` 返回的就是可上传 JSON，字段名与
   ``maimai.user_data.PlayInput`` 一致 —— 这个桥在写 ``/估分`` 时就留好了
   （当时的注释是「留着接口等产品决定下一轮拿它做什么」，2026-09-27 定为传分用）。
2. **用户当场给的上传字段**：``musicId level achievement [deluxscoreMax]
   [comboStatus] [syncStatus] [maxCombo]``。

判定明细不能全 0
================
``noteCounts`` 全 0 却带着达成率 / DX 分，是一份**自相矛盾的 playlog**
（0 个 note 却有成绩）。``maimai.user_data.generate_playlog`` 会告警，
且 2026-09-25 实测这种报文**连同 ``userMusicDetailList`` 一起没有落库**。

用户只给摘要字段时拿不到判定明细，所以 :func:`fill_note_counts` 会用
**曲库物量 + 现有估算器**补一份与给定 ``achievement`` / ``comboStatus``
自洽的分布 —— 曲库里查不到这首歌、或估算器取不到解时，原样上传并在回复里
明确说明（不静默）。

为什么这个模块可以 ``import maimai``
====================================
``maimai/`` **不随仓库分发**（``.gitignore`` 与 ``.dockerignore`` 都排除了它），
镜像里通常没有这个包。所以本模块**只在 ``#上传`` 被调用时**才被 import
（见 :func:`liz_bot.command_router._upload_module`）—— 在模块级 import 它，
整个机器人会起不来。import 失败就是一句「上传功能不可用」。

⚠️ 连 ``maimai.config`` 都会在 import 期读 ``.env`` 并**对必填项抛
``RuntimeError``**，而 ``.env`` 也在 ``.dockerignore`` 里 ⇒ 探测可用性时
不能只接 ``ImportError``（见 :func:`liz_bot.command_router._upload_module`）。

请求量守卫
==========
一次上传约 **16** 次请求（``include_items=False``，见 :func:`upload` 的说明）。
舞萌服务端**按请求量封禁**
（同次会话 ≥1000 条即 ban，见项目记忆），所以这里加了**单飞 + 冷却**：
同时只跑一次，两次之间至少隔 :data:`COOLDOWN_SECONDS` 秒。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from liz_bot import judge_detail, replies, score_estimate, song_query
from liz_bot.text_layout import char_width, display_width

from maimai import user_data as UD
from maimai import workflow as wf
from maimai.client import MaimaiClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 扫卡二维码前缀与标准长度（``8 + 12 + 64``，见 ``maimai/qr.py``）。
QR_PREFIX = "SGWCMAID"
QR_MIN_LEN = 84

#: 两次上传之间至少间隔的秒数 —— 见模块 docstring 的「请求量守卫」。
COOLDOWN_SECONDS = 30.0

#: 模拟游玩时长（秒）。对齐 ``_tools/live_upload.py`` 的默认值，
#: 让 playlog 的 ``playDate`` 与登录时刻拉开一段像真实游玩的间隔。
#: 可用 ``MM_UPLOAD_DURATION`` 覆盖（``0`` = 不等，调试用）。
DEFAULT_PLAY_DURATION = 60.0

#: 是否拉道具/礼物。**默认关** —— 道具按种类分页，请求数明显更多，
#: 而传分只需要成绩通道。对齐「请求量越低越好」的约束。
INCLUDE_ITEMS = False

#: 每条回复的显示宽度上限（格）。与项目其它排版一致（CJK 算 2 格）。
LINE_WIDTH = 39

#: 合法的难度编号 —— 与 ``maimai.typings.MusicDifficultyID`` 一致。
#: 0-4 是常规五档，``10`` 是宴谱（Utage）。
#:
#: ⚠️ ``10`` **能解析但传不了**（见 :func:`handle_upload` 的宴谱闸门）。
#: 留着它不是为了支持，而是为了让「``#上传 … 10 …``」得到一句专门的
#: 「宴谱传不了」，而不是撞上 ``maimai.upload_bad_level`` 的
#: 「难度要填 0-4」—— 后者会让人以为是自己写错了。
VALID_LEVELS = frozenset({0, 1, 2, 3, 4, 10})

#: 干跑用的占位 userId。``.env`` 的 ``USER_ID`` 常为空（二维码流程不需要它），
#: 而 ``run_workflow`` 要求「qr_code 或 user_id 至少有一个」，否则会在初始化阶段
#: 直接失败。与 ``_tools/live_upload.py`` 的 ``_DRY_USER_ID`` 同一个值。
_DRY_USER_ID = 12345678901

#: 干跑时冒充的 token —— 同上，让 ``run_workflow`` 走「已有凭据」那条分支。
_DRY_TOKEN = "dry-token"


# ---------------------------------------------------------------------------
# 二维码
# ---------------------------------------------------------------------------

def looks_like_qr(token: str) -> bool:
    """这个 token 像不像扫卡二维码。

    判据是**前缀或长度** —— 宁可宽进（后面 ``qr_api`` 还会再校验一次），
    也不要因为用户少打几个字符就把他引到「你没给二维码」的错误分支上。
    """
    return token.startswith(QR_PREFIX) or len(token) >= QR_MIN_LEN


def mask_qr(qr: str) -> str:
    """脱敏显示二维码 —— **它等同一张卡**，任何回复 / 日志里都不能出现全量。"""
    return qr if len(qr) <= 8 else f"{qr[:4]}…{qr[-4:]}"


# ---------------------------------------------------------------------------
# 成绩字段
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoreField:
    """一个上传字段的元数据（**顺序即位置参数顺序**）。"""

    name: str
    required: bool
    low: int
    high: int
    #: 报「超出范围」时给用户看的边界写法（``None`` = 直接用 ``low`` / ``high``）。
    #:
    #: ⚠️ **当前没有任何渲染路径在用它们**（2026-09-27 起）：用户要求
    #: ``maimai.upload_range`` 不再写范围，``_range_text`` 已随之删除。
    #: 这两个字段**刻意留着** —— 它们是「这个字段的边界在哪」的事实记录，
    #: ``low`` / ``high`` 本身仍在校验；哪天想把范围提示加回去，改文案即可，
    #: 不必再推一遍 1/10000 % 的单位换算。
    #: （当初只有 ``achievement`` 用得上：内部单位是 1/10000 %，而用户写的是
    #: 百分比，直接把 ``1`` 和 ``1010000`` 摆出来只会让人更困惑。）
    low_text: str | None = None
    high_text: str | None = None


#: 位置参数顺序。前三个必填，其余可省（省掉就是 ``PlayInput`` 的默认值）。
SCORE_FIELDS: tuple[ScoreField, ...] = (
    ScoreField("musicId", True, 1, 99999),
    ScoreField("level", True, 0, 10),
    ScoreField("achievement", True, 1, 1010000,
               low_text="0.0001%", high_text="101%"),
    ScoreField("deluxscoreMax", False, 0, 9999999),
    ScoreField("comboStatus", False, 0, 4),
    ScoreField("syncStatus", False, 0, 5),
    ScoreField("maxCombo", False, 0, 999999),
)

#: 必填字段个数 —— 参数给少了要报「还差几个」，不能等到上传时才炸。
REQUIRED_FIELDS = sum(1 for f in SCORE_FIELDS if f.required)

#: ``achievement`` 用**百分比写法**时的上界。达成率最高 101%。
#:
#: 有了它，两种写法就能**按大小自动区分**、不会歧义：``100.8750`` 是百分比
#: （原值形态下 100.8750 不可能是整数），``1008750`` 是协议原值。而 ``101``
#: 只可能是「101%」—— 原值 101 表示 0.0101%，没人会想传那个。
_ACHIEVEMENT_PERCENT_MAX = 101


def _parse_achievement(raw: str) -> int | None:
    """达成率 → 协议原值（1/10000 %）；看不懂返回 ``None``。

    收两种写法（**推荐百分比**，与 ``/估分`` 一致）::

        100.8750   → 1008750
        100        → 1000000
        100.8750%  → 1008750
        1008750    → 1008750      （协议原值，兼容老写法）

    ⚠️ **不能直接把整数丢给** :func:`score_estimate.parse_percent` ——
    它会把 ``"1008750"`` 读成 1008750%。

    两种写法靠**大小**分开，且刻意留了一道缝（``101 < v < 10000`` 一律拒收）：
    ``200`` 到底是「200%」（超上限）还是「0.02%」（原值）？——**两者都不该被
    猜**，报「看不懂」比猜错安全。
    """
    text = raw.strip()
    plain = text.rstrip("%").strip()
    if "." in plain or (
        plain.isdigit() and int(plain) <= _ACHIEVEMENT_PERCENT_MAX
    ):
        return score_estimate.parse_percent(text)
    if plain.isdigit() and int(plain) >= 10000:
        return int(text)                       # 协议原值（≥ 1%）
    return None


def _parse_level(raw: str) -> int | None:
    """难度 → 0-4（或 ``10`` 宴谱）；看不懂返回 ``None``。

    写法与 ``/songdata`` 完全一致（``judge_detail.resolve_difficulty``）：
    ``0``-``4``、``basic``…``remas``、``绿``/``黄``/``红``/``紫``/``白``。
    ``10``（宴谱）不在那张别名表里，单独按数字收。
    """
    named = judge_detail.resolve_difficulty(raw)
    if named is not None:
        return named
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_combo(raw: str) -> int | None:
    """连击等级 → ``0``-``4``。写法与 ``/估分`` 一致（``FC`` / ``FC+`` / ``AP`` / ``AP+``）。"""
    return score_estimate.parse_combo(raw)


def _parse_sync(raw: str) -> int | None:
    """同步状态 → ``0``-``5``。收数字，也收显示名（``FS`` / ``FDX+`` / ``同步游玩``）。

    显示名就是 :data:`liz_bot.replies` 的 ``maimai.sync_names``（下标即协议值），
    所以两边永远同步 —— 改名字只动 ``replies.json``。
    """
    text = raw.strip()
    if text.isdigit():
        return int(text)
    names = replies.get("maimai.sync_names")
    lowered = text.lower()
    for index, name in enumerate(names):
        if name.lower() == lowered:
            return index
    return None


#: 字段名 → 专用解析器。**没登记的走 ``int()``**（``musicId`` / ``deluxscoreMax``
#: / ``maxCombo`` 就是纯数字）。分开写是为了让「每个字段收什么写法」一眼可见。
_FIELD_PARSERS: dict[str, Callable[[str], int | None]] = {
    "level": _parse_level,
    "achievement": _parse_achievement,
    "comboStatus": _parse_combo,
    "syncStatus": _parse_sync,
}


def parse_score_params(tokens: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    """把位置参数解析成成绩 dict。

    :returns: ``(score, None)`` 或 ``(None, 已渲染的错误文案)``。
        返回**渲染好的文案**而不是键 —— 与 :func:`liz_bot.score_estimate.estimate`
        的约定一致，调用方不必再知道每个键要什么占位符。
    """
    if len(tokens) < REQUIRED_FIELDS:
        missing = [f.name for f in SCORE_FIELDS[len(tokens):] if f.required]
        return None, replies.text(
            "maimai.upload_need_field",
            count=len(missing),
            fields=" / ".join(missing),
            usage=replies.text("maimai.upload_usage_fields"),
        )

    score: dict[str, Any] = {}
    for field_meta, raw in zip(SCORE_FIELDS, tokens):
        parser = _FIELD_PARSERS.get(field_meta.name)
        if parser is not None:
            value = parser(raw)
        else:
            try:
                value = int(raw)
            except ValueError:
                value = None

        if value is None:
            return None, replies.text(
                "maimai.upload_bad_field", field=field_meta.name, value=raw
            )
        if field_meta.name == "level" and value not in VALID_LEVELS:
            # ⚠️ 文案里**刻意不提** ``10``（2026-09-27）—— 宴谱虽然能解析，
            #    但紧接着就会被闸门挡下，写进「可填值」只会把人引到坑里。
            return None, replies.text("maimai.upload_bad_level", value=value)
        if not field_meta.low <= value <= field_meta.high:
            # ⚠️ 刻意**不报范围**（2026-09-27 用户要求）—— 见 ScoreField.low_text。
            return None, replies.text(
                "maimai.upload_range", field=field_meta.name, value=raw,
            )
        score[field_meta.name] = value
    return score, None


def score_from_cache(session_key: str | None) -> dict[str, Any] | None:
    """取该会话最近一次 ``/估分`` 的结果，转成可上传 JSON。

    没有缓存 / 已超时 / 无会话键都返回 ``None``（**不是错误** ——
    「没缓存」与「缓存坏了」对用户是同一件事：该自己给字段了）。
    """
    est = score_estimate.CACHE.get(session_key)
    if est is None:
        return None
    return est.payload()


# ---------------------------------------------------------------------------
# 判定明细补全
# ---------------------------------------------------------------------------

def _percent_text(achievement: int) -> str:
    """``995989`` → ``"99.5989"``。

    ⚠️ **不能直接把整数丢给 :func:`score_estimate.parse_percent`** ——
    它把 ``"995989"`` 读成 995989%（再乘 10000），会得到一个天文数字的达成率。
    必须先按「1/10000 %」还原成小数写法。
    """
    return f"{achievement // 10000}.{achievement % 10000:04d}"


def fill_note_counts(score: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """补判定明细（以及**由它决定的**那几个字段），返回 ``(新成绩, 说明或 None)``。

    只在 ``noteCounts`` 缺失时动手：拿曲库物量 + :func:`score_estimate.estimate`
    求一份与给定达成率 / 连击等级自洽的分布。曲库没有这首歌（或估算器取不到解）
    就原样返回，并给出一句「明细补不出来」的说明 —— 由调用方决定怎么呈现
    （这里不抛异常，因为「补不出来」不是致命错误）。

    补的不只是明细
    --------------
    ``noteCounts`` 一旦补上，下面三个字段就**必须跟着自洽**，否则写出的是一份
    自相矛盾的 playlog（服务端可能整份丢弃，2026-09-25 实测过）：

    * ``maxCombo`` —— ``PlayInput`` 省略时是 ``0``，而 ``totalCombo`` 会按明细
      求和；「有连击但最大连击为 0」会触发 ``generate_playlog`` 的告警。
    * ``comboStatus`` —— ``0`` 的语义是「**有 Miss**」，与一份没有 Miss 的明细
      直接打架。
    * ``deluxscoreMax`` —— ``0`` 意味着「一颗 CP 都没有」，而 100% 以上的达成率
      必然要求绝大多数音符是 CP / P。

    ⚠️ **用户明确给了的字段一律不覆盖** —— 他知道自己那局打成什么样，我们只补
    他没说的。补了哪些会写进返回的说明里，不静默。
    """
    if score.get("noteCounts"):
        return score, None

    # ⚠️ combo 只有**用户明确给了**才当约束传进去 —— 省略（``None``）是
    #    「不限」，而 ``0`` 是一个真约束（结果里必须出现 Miss）。把「没给」
    #    当成 ``0`` 会强行塞一个 Miss 进分布，那是凭空捏造。
    raw_combo = score.get("comboStatus")
    combo_arg = "" if raw_combo is None else str(raw_combo)

    try:
        est, error = score_estimate.estimate(
            str(score["musicId"]),
            str(score["level"]),
            _percent_text(int(score["achievement"])),
            "",                                # 星级不限 —— 只求「有解」
            combo_arg,
        )
    except Exception:  # noqa: BLE001 —— 见下
        # ⚠️ **不能让估算器把整条指令带走**。它的契约是「返回 (None, 文案)」，
        # 但真实踩过一次它抛 ``ValueError``（``stars_of`` 的 ``max()`` 空序列，
        # 2026-09-27 已修）。补明细是**尽力而为**的一步：失败就退回「没补上」，
        # 让用户至少还能拿到那句「全 0 的 playlog 可能被丢弃」的告警。
        logger.warning("传分：估算器异常，判定明细未补", exc_info=True)
        est, error = None, None

    if est is None:
        lines = [replies.text("maimai.upload_note_missing")]
        # 复用估算器渲染好的原因（「没找到可行的判定分布」等）——
        # 只说「补不出来」用户不知道该改哪个参数。
        #
        # ⚠️ **唯独 ``song.not_found`` 不再贴一遍**：曲库没这首歌时，
        #    标题行（``upload_title_plain``）用的就是**同一句**
        #    「Liz没有找到这样的歌」，紧跟其后又贴一次就是逐字重复
        #    （2026-09-27 用户要求两处「均沿用」这一句之后暴露出来的）。
        #    比对是安全的：这条文案**没有占位符**，渲染结果就是原文。
        if error and error != replies.text("song.not_found"):
            lines.append(error)
        return score, "\n".join(lines)

    counts = dict(est.counts)
    filled = dict(score)
    filled["noteCounts"] = counts

    derived: list[str] = []
    if filled.get("maxCombo") is None:
        filled["maxCombo"] = score_estimate.max_combo(counts)
        derived.append("maxCombo")
    if filled.get("comboStatus") is None:
        filled["comboStatus"] = score_estimate.combo_status(counts)
        derived.append("combo")
    if filled.get("deluxscoreMax") is None:
        filled["deluxscoreMax"] = est.solution.deluxscore
        derived.append("dx分")

    lines = [replies.text("maimai.upload_note_filled", count=est.note_total)]
    if derived:
        lines.append(
            replies.text("maimai.upload_note_derived", fields="、".join(derived))
        )
    return filled, "\n".join(lines)


# ---------------------------------------------------------------------------
# 展示用的一行
# ---------------------------------------------------------------------------

def describe(score: dict[str, Any]) -> str:
    """成绩的第一行：``【传分】Fragrance Re:Master 14+``。

    曲库查不到就退回「只有难度」的写法 —— 传分不依赖曲库，查不到不该让整条
    指令失败。**id 不在这行**（它在成绩摘要那行开头，见 :func:`render`）：
    曲名长了这行必然折行，把 id 放末尾会被折成「id 14」+「3」。
    """
    music_id = int(score["musicId"])
    level = int(score["level"])
    names = judge_detail.difficulty_names()
    difficulty = names[level] if 0 <= level < len(names) else str(level)

    hits = None
    try:
        hits = song_query.select_song(str(music_id), song_query.BY_ID)
    except (OSError, ValueError):
        # 曲库读不动 —— 与 _search_songs 同样的判据，只影响这一行的信息量
        logger.warning("传分：曲库载入失败，标题退回难度", exc_info=True)

    if hits:
        song = hits[0]["song"]
        levels = song.get("level") or []
        chart_level = levels[level] if level < len(levels) else None
        return replies.text(
            "maimai.upload_title",
            title=song.get("title") or "?",
            difficulty=difficulty,
            level=chart_level if chart_level is not None else "-",
        )
    return replies.text("maimai.upload_title_plain", difficulty=difficulty)


def _wrap_cells(text: str, limit: int = LINE_WIDTH) -> list[str]:
    """按显示宽度折行（CJK 算 2 格）—— 手机 QQ 气泡约 32-42 格。

    项目里只有「按像素折行」（``help_image._wrap``，给图片用）与
    ``text_layout`` 的 ``pad`` / ``truncate``；纯文本折行这里自己写一个，
    免得为了折几行字把 Pillow 拖进运行期依赖。
    """
    lines: list[str] = []
    current = ""
    width = 0
    for char in text:
        if char == "\n":
            lines.append(current)
            current, width = "", 0
            continue
        char_w = char_width(char)
        if width + char_w > limit and current:
            lines.append(current)
            current, width = "", 0
        current += char
        width += char_w
    lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# 带探针的执行
# ---------------------------------------------------------------------------

@dataclass
class UploadReport:
    """一次上传的全部可汇报信息。"""

    ok: bool
    stage: str
    message: str
    score: dict[str, Any]
    note: str | None = None
    before: int | None = None
    after: int | None = None
    calls: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)

    @property
    def landed(self) -> bool | None:
        """是否**确认落库**；回读拿不到数据时 ``None``（未知，不是失败）。"""
        if self.before is None or self.after is None:
            return None
        return self.after > self.before


class ProbeClient(MaimaiClient):
    """带探针的客户端：记接口调用，并在**同一会话内**回读 ``playCount``。

    为什么必须回读：``returnCode == 1`` **不等于落库**（2026-09-25 实测）。
    而回读必须在同一会话 —— ``GetUserMusicApi`` 只认登录 cookie，会话一断就得
    重新登录，**连续两次登录会锁号 15 分钟**（见 ``maimai/workflow.py``）。
    所以回读点只能选在会话还活着的那两个瞬间：``UserLoginApi`` 之后、
    ``UpsertUserAllApi`` 之后。
    """

    def __init__(self, music_id: int, level: int) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.before: int | None = None
        self.after: int | None = None
        self._target = (int(music_id), int(level))
        self._reading = False

    async def request(self, api_name, data, user_id=None):  # noqa: ANN001, ANN201
        response = await super().request(api_name, data, user_id)
        self.calls.append(api_name)
        if self._reading:
            return response                      # 回读自己发的请求不触发回读
        if api_name == "UserLoginApi" and response.get("returnCode") == 1:
            self.before = await self._readback(user_id)
        elif api_name == "UpsertUserAllApi":
            self.after = await self._readback(user_id)
        return response

    async def _readback(self, user_id: int | None) -> int | None:
        """读回本曲本难度的 ``playCount``；查不到记 0（= 这首还没打过）。"""
        if user_id is None:
            return None
        self._reading = True
        try:
            details = await UD.UserData(
                user_id=user_id, client=self
            )._fetch_music()  # noqa: SLF001 —— 与 _tools/live_upload.py 同一做法
        except Exception:  # noqa: BLE001 —— 回读失败不能掩盖上传本身的结果
            logger.warning("传分：回读 playCount 失败", exc_info=True)
            return None
        finally:
            self._reading = False
        for row in details or []:
            key = (int(row.get("musicId") or 0), int(row.get("level") or 0))
            if key == self._target:
                return int(row.get("playCount") or 0)
        return 0


class _DryClient:
    """干跑用的假 client —— 不联网，但把 ``run_workflow`` 全阶段走完。

    形状对齐 ``_tools/live_upload.py`` 的 ``install_dry``，另外补了
    :class:`ProbeClient` 那套探针（记接口调用 + 模拟落库回读），这样干跑出来的
    :class:`UploadReport` 与真跑**结构完全一致** —— 回复里不会出现
    「接口 0 次」「回读失败」这种只因为干跑才有的噪声，否则看的人分不清
    「干跑没探针」和「真跑探不到」。

    **刻意只实现 ``run_workflow`` 与 ``UserData.reload`` 真正会调的接口**：
    将来上游多调一个接口，干跑会立刻报错 —— 它顺带成了「接口清单变了」的探针。
    """

    retries = 0

    def __init__(self, music_id: int, level: int) -> None:
        self._target = (int(music_id), int(level))
        self._play_count = 36
        self.calls: list[str] = []
        self.before: int | None = None
        self.after: int | None = None

    async def request(self, api_name, data, user_id=None):  # noqa: ANN001, ANN201
        self.calls.append(api_name)
        if api_name == "GetGameSettingApi":
            return {"gameSetting": {"requestInterval": 0, "isMaintenance": False}}
        if api_name == "GetUserPreviewApi":
            return {"userId": user_id, "isLogin": False}
        if api_name == "UserLoginApi":
            self.before = self._play_count
            return {"returnCode": 1, "loginId": 987654, "loginDateTime": int(time.time())}
        if api_name == "UserLogoutApi":
            return {"returnCode": 1}
        if api_name == "UpsertUserAllApi":
            self._play_count += 1            # 干跑模拟「确实落库」
            self.after = self._play_count
            return {"returnCode": 1}
        if api_name == "GetUserMusicApi":
            return {
                "userMusicList": [{"userMusicDetailList": [
                    {"musicId": self._target[0], "level": self._target[1],
                     "playCount": self._play_count, "achievement": 0},
                ]}],
                "nextIndex": 0,
            }
        if api_name == "GetUserExtendApi":
            return {"userExtend": {
                k: 0 for k in UD._EXTEND_FIELDS  # noqa: SLF001
                if k not in ("isPhotoAgree", "isGotoCodeRead")
            }}
        if api_name == "GetUserDataApi":
            return {"userData": {"lastRomVersion": "1.56.00",
                                 "lastDataVersion": "1.55.04",
                                 "playCount": 36, "currentPlayCount": 36},
                    "banState": 0}
        if api_name == "GetUserOptionApi":
            return {"userOption": {}}
        if api_name == "GetUserRatingApi":
            return {"userRating": {}}
        if api_name == "GetUserChargeApi":
            return {"userChargeList": []}
        if api_name == "GetUserActivityApi":
            return {"userActivity": {}}
        if api_name == "GetUserCharacterApi":
            return {"userCharacterList": []}
        if api_name == "GetUserItemApi":
            return {"userItemList": [], "nextIndex": 0}
        if api_name == "GetUserMissionDataApi":
            return {"userWeeklyData": {}, "userMissionDataList": []}
        return {}

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# 单飞 + 冷却
# ---------------------------------------------------------------------------

#: 同时只允许一次上传。串行化的理由不是性能，是**服务端按请求量封禁**：
#: 群里多人同时刷 ``#上传`` 会把 16 次请求叠成几十上百次。
_UPLOAD_LOCK = asyncio.Lock()
_cooldown_until = 0.0


def busy() -> bool:
    """是否已有一次上传在跑。"""
    return _UPLOAD_LOCK.locked()


def cooldown_left() -> float:
    """还要等多少秒才能再传（0 = 可以传）。"""
    return max(0.0, _cooldown_until - time.monotonic())


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

async def upload(
    score: dict[str, Any],
    qr: str,
    *,
    note: str | None = None,
    dry: bool = False,
) -> UploadReport:
    """跑一次完整上传。**调用方必须先确认 :func:`busy` 为假。**

    :param score: 成绩 dict（见模块 docstring 的字段表）
    :param qr: 扫卡二维码。**干跑时不使用**（改用假的 token + userId，见下）
    :param note: 判定明细补全 / 缺失的说明，原样带进 :class:`UploadReport`
    :param dry: 不联网，用假 client 走完整流程
    """
    global _cooldown_until

    async with _UPLOAD_LOCK:
        try:
            duration = float(os.environ.get("MM_UPLOAD_DURATION")
                             or DEFAULT_PLAY_DURATION)
        except ValueError:
            duration = DEFAULT_PLAY_DURATION

        # 二维码只以**脱敏**形式进日志 —— 它等同一张卡（见 :func:`mask_qr`）。
        # 记这一行是为了事后能对上「哪一次上传用了哪个码」。
        logger.info(
            "传分开始：musicId=%s level=%s qr=%s dry=%s duration=%.0fs",
            score.get("musicId"), score.get("level"), mask_qr(qr), dry, duration,
        )

        if dry:
            client: Any = _DryClient(int(score["musicId"]), int(score["level"]))
            # ⚠️ 干跑**绝不能**把二维码交给 ``run_workflow`` —— 给了它就会真的去
            # 调 ``qr_api``（联网）。改走「已有凭据」那条分支：假 client 会把
            # userId / token 原样回显，与 ``_tools/live_upload.py`` 的 install_dry
            # 完全一致。
            credentials: dict[str, Any] = {
                "qr_code": None, "token": _DRY_TOKEN, "user_id": _DRY_USER_ID,
            }
            play_duration = 0.0
        else:
            client = ProbeClient(int(score["musicId"]), int(score["level"]))
            credentials = {"qr_code": qr or None}
            play_duration = duration

        try:
            result = await wf.run_workflow(
                score,
                include_items=INCLUDE_ITEMS,
                play_duration=play_duration,
                client=client,
                **credentials,
            )
        except Exception as exc:  # noqa: BLE001 —— 见下
            # ``run_workflow`` 自己会接住 ``MaimaiApiError`` / ``QrApiError`` /
            # ``ValueError`` 并返回 ``ok=False``，所以走到这里的是**意料之外**的
            # 异常（网络层、上游改动……）。转成一份失败报告而不是往上抛：
            # 用户要的是「卡在哪一步」，而不是 ``bot.error`` 里那句被截断到
            # 20 字的异常名。阶段名取**最后发出的那个接口** —— 那是我们手头
            # 最接近现场的线索（``stages`` 是 ``run_workflow`` 的局部变量）。
            calls = list(getattr(client, "calls", []))
            logger.exception("传分：run_workflow 抛出未预期异常")
            result = wf.WorkflowResult(
                ok=False,
                stage=calls[-1] if calls else "初始化",
                message=f"{type(exc).__name__}: {exc}",
            )
        finally:
            # 冷却从「本次结束」算起 —— 无论成败。失败后立刻重试同样危险。
            _cooldown_until = time.monotonic() + COOLDOWN_SECONDS
            # ⚠️ **必须自己关**。``run_workflow`` 只在 ``client=None``（它自己
            # 造的那个）时调 ``aclose``；我们**传了** client 进去，它认为所有权
            # 在我们手上 ⇒ 不关就每次上传漏一个 ``httpx.AsyncClient``。
            close = getattr(client, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001 —— 关连接失败不该影响结果
                    logger.warning("传分：关闭 client 失败", exc_info=True)

        # 阶段轨迹是**权威轨迹**（见 run_workflow 的 docstring）：`ok=True` 也要看
        # 它才知道会话有没有真正释放（末尾那项是 UserLogout 还是「UserLogout 失败」）。
        logger.info(
            "传分结束：ok=%s stage=%s 接口=%d 次 轨迹=%s",
            result.ok, result.stage, len(getattr(client, "calls", [])),
            " → ".join(result.stages),
        )
        if not result.ok:
            logger.warning("传分失败：%s", result.message)

        return UploadReport(
            ok=result.ok,
            stage=result.stage,
            message=result.message,
            score=score,
            note=note,
            before=getattr(client, "before", None),
            after=getattr(client, "after", None),
            calls=list(getattr(client, "calls", [])),
            stages=list(result.stages),
        )


# ---------------------------------------------------------------------------
# 指令入口
# ---------------------------------------------------------------------------

def dry_mode() -> bool:
    """``MM_DRY`` 有值 ⇒ 干跑（与 ``_tools/live_upload.py`` 同一个开关）。

    干跑**不联网**，但会把 ``run_workflow`` 的每个阶段走完，顺带验证
    「补判定明细 → 上传 → 回读 playCount」这条链自身没坏。
    """
    return bool(os.environ.get("MM_DRY"))


#: 预估总耗时时额外加上的**节流开销**（秒）—— 一次上传 16 次接口调用，
#: 每次之间要留间隔（服务端按请求量封禁，见模块 docstring 的「请求量守卫」）。
_INTRO_OVERHEAD_SECONDS = 20


def intro_text() -> str:
    """上传**开始前**那句「大概要等多久」（文案见 ``maimai.upload_intro``）。

    时长 = ``MM_UPLOAD_DURATION``（默认 60s 的模拟游玩）+ 16 次请求的节流开销。
    刻意**不**在干跑时调用 —— 干跑不联网、不节流，几毫秒就完，说了反而误导
    （见 :func:`handle_upload`）。
    """
    try:
        duration = float(os.environ.get("MM_UPLOAD_DURATION")
                         or DEFAULT_PLAY_DURATION)
    except ValueError:
        duration = DEFAULT_PLAY_DURATION
    return replies.text(
        "maimai.upload_intro", time=f"{int(duration) + _INTRO_OVERHEAD_SECONDS} 秒"
    )


async def handle_upload(cmd_params, session_key, *, notify=None) -> str:
    """``#上传 <二维码> [<成绩字段…>]`` —— :mod:`liz_bot.command_router` 调这个。

    参数怎么给（**二维码必须放第一个**）::

        #上传 <二维码>                     → 用 ``/估分`` 的会话缓存
        #上传 <二维码> <字段…>              → 用当场给的字段（不看缓存）

    二维码为什么不从缓存来：它**扫一次就失效**（约 10 分钟，连只读请求也会
    到期），缓存一张码只会给用户「看着能用、其实不能用」的假象。

    判断的**先后顺序**是有讲究的
    ----------------------------
    1. **先判二维码** —— 「你没给码」比「正在忙」更贴近用户当下要改的东西，
       而且这个判断不需要任何计算。
    2. **再定成绩来源**（解析参数 / 读缓存）—— 这一步只做字符串解析与查表，
       **不跑 DP**。放在闸门之前，是为了让「参数写错了」「宴谱传不了」这类
       **与忙不忙无关**的结论立刻出来，而不是先让用户等 30 秒冷却、
       等完了才被告知参数本身就不行。
    3. **宴谱（``level=10``）在这里就挡下** —— 它必须能被解析（否则用户写
       ``10`` 会撞上「难度要填 0-4」，答非所问），但曲库拿不到宴谱的物量、
       补不出判定明细，传上去是一份空 playlog（服务端可能整份丢弃）。
       见 :data:`VALID_LEVELS`。
    4. **忙碌 / 冷却挡在补判定明细之前** —— 补明细要跑一次估分（有界背包 DP），
       反正这次也传不了，没必要白跑。
    5. **给了字段就一定走「字段」这条路**（哪怕给少了）—— 掉回缓存会把用户
       刚打的字悄悄丢掉，而「还差几个字段」才是他真正需要看到的。

    :param notify: ``async (text) -> None`` —— **任务开始前先发一条**的通路
        （由 ``qqgroupbot`` 注入，走被动回复 ``msg_seq=2``，不消耗主动消息配额）。
        真跑一次约 80 秒（60s 模拟游玩 + 16 次请求的节流），这期间用户什么都
        收不到，所以闸门全过、确定要传时先发一句预计等待时长
        （见 :func:`intro_text`）。``None`` = 不发，自检脚本与等价性测试走这条。
    """
    params = list(cmd_params or [])
    usage = replies.text("maimai.upload_usage")

    if not params or not looks_like_qr(params[0]):
        return replies.text("maimai.upload_need_qr", usage=usage)
    qr, rest = params[0], params[1:]

    # ---- 成绩从哪来（纯解析，不跑 DP）----
    if rest:
        score, error = parse_score_params(rest)
        if score is None:
            return error or usage
        source = replies.text("maimai.upload_source_params")
    else:
        score = score_from_cache(session_key)
        if score is None:
            return replies.text("maimai.upload_no_cache", usage=usage)
        source = replies.text("maimai.upload_source_cache")

    # ---- 宴谱：能解析，但传不了（见 docstring 第 3 条）----
    if int(score.get("level", 0)) == 10:
        return replies.text("maimai.upload_no_utage")

    if busy():
        return replies.text("maimai.upload_busy")
    waiting = cooldown_left()
    if waiting > 0:
        return replies.text("maimai.upload_cooldown", seconds=int(waiting) + 1)

    dry = dry_mode()
    # 闸门全过了、确定要传 —— 先把「大概要多久」告诉用户（真跑约 80 秒）。
    # ⚠️ 干跑**不发**：它不联网、不节流，几毫秒就完，报个「80 秒」是误导。
    # ⚠️ 发不出去也**不该挡住上传** —— 预告只是锦上添花，转成告警继续。
    if notify is not None and not dry:
        try:
            await notify(intro_text())
        except Exception:  # noqa: BLE001
            logger.warning("传分：预告消息发送失败，继续上传", exc_info=True)

    score, note = fill_note_counts(score)
    report = await upload(score, qr, note=note, dry=dry)
    return render(report, source=source, dry=dry, cooldown=cooldown_left())


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _combo_label(value: int) -> str:
    return score_estimate.COMBO_NAMES.get(value, str(value))


def _sync_label(value: int) -> str:
    names = replies.get("maimai.sync_names")
    return names[value] if 0 <= value < len(names) else str(value)


def render(
    report: UploadReport,
    *,
    source: str,
    dry: bool = False,
    cooldown: float | None = None,
) -> str:
    """把 :class:`UploadReport` 渲染成回复文本（每行 ≤ :data:`LINE_WIDTH` 格）。

    成绩摘要**刻意拆成两行**：``id … · 达成 …% · DX …`` 与
    ``combo … · sync … · 最大连击 …``。挤成一行是 45 格（超过 39 格上限），
    会被 :func:`_wrap_cells` 从中间切开，变成「… · DX 1965 · com」+「bo AP …」
    这种一眼读不通的样子。

    ``id`` 放在**第二行开头**（而不是标题行末尾）：曲名长了标题行必然折行，
    放末尾就可能被折成「id 14」+「3」—— 数字被切开比折行本身更糟。
    """
    score = report.score
    lines: list[str] = [
        describe(score),
        replies.text(
            "maimai.upload_fields",
            music_id=score.get("musicId", 0),
            achievement=_percent_text(int(score["achievement"])) + "%",
            dx=score.get("deluxscoreMax", 0),
        ),
        replies.text(
            "maimai.upload_state",
            combo=_combo_label(int(score.get("comboStatus", 0))),
            sync=_sync_label(int(score.get("syncStatus", 0))),
            max_combo=score.get("maxCombo", 0),
        ),
        source,
    ]
    if report.note:
        lines.append(report.note)
    if dry:
        lines.append(replies.text("maimai.upload_dry"))

    if report.ok:
        lines.append(replies.text("maimai.upload_ok"))
    else:
        lines.append(replies.text("maimai.upload_fail"))
        lines.extend(_wrap_cells(report.message))

    landed = report.landed
    if landed is None:
        lines.append(replies.text("maimai.upload_no_readback"))
    elif landed:
        lines.append(replies.text(
            "maimai.upload_landed", before=report.before, after=report.after
        ))
    else:
        lines.append(replies.text("maimai.upload_not_landed"))
    if cooldown is not None and cooldown > 0:
        lines.append(replies.text(
            "maimai.upload_cooldown_hint", seconds=int(cooldown) + 1
        ))

    out: list[str] = []
    for line in lines:
        out.extend(_wrap_cells(line))
    return "\n".join(out)


def line_width_ok(text: str) -> bool:
    """回复的每一行是否都在 :data:`LINE_WIDTH` 格以内（自检用）。"""
    return all(display_width(line) <= LINE_WIDTH for line in text.splitlines())
