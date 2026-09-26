"""回复文本 —— 用户可见文案集中从 JSON 文件读取，便于调试与修改。

为什么要有这个模块
------------------
查歌、查询别名等指令的提示文案原先硬编码在各业务模块里（``song_query`` 的
``NOT_FOUND``、``command_router`` 的「未知指令」、``daily_funcs`` 的问候语……）。
改一个字要翻好几个源码文件，还容易漏改，导致同一句提示在不同分支下不一致。

现在它们**全部**来自::

    liz_bot/texts/replies.json

改文案只动这一个文件，**不用改代码、不用重启**（见下面的「热更新」）。

文件格式
--------
按业务模块分组的**两层** JSON，叶子值只能是**字符串**或**字符串数组**::

    {
      "song": {
        "not_found": "Liz没有找到这样的歌",
        "format": "\\n乐曲名称：{title} \\n曲师：{artist} ..."
      },
      "bot": {
        "none_reply": ["干什么！", "Liz在哦"]
      }
    }

取值用点号路径::

    replies.text("song.not_found")            # -> "Liz没有找到这样的歌"
    replies.get("bot.none_reply")             # -> ["干什么！", ...]
    replies.text("song.format", title=..., artist=...)   # 模板渲染

占位符
------
带 ``{名字}`` 的值是模板。**允许的占位符名由本模块的 :data:`_SCHEMA` 声明**，
写错会在载入时立刻报错，而不是等某条消息触发时才炸。
需要输出**字面量**花括号时写 ``{{`` / ``}}``。

热更新
------
每次取值前 ``stat`` 一次文件的 mtime（微秒级开销），mtime 变了就重新载入。
所以「改文件 → 群里发一条消息」即可看到新文案，调试期不必反复重启。
:func:`reload` 可强制重载。

缺失即报错，绝不静默回退
------------------------
文件不存在、JSON 损坏、缺键、类型不对、占位符写错 —— 一律抛
:class:`RepliesError` 并附修复提示。**刻意不内置一份"代码里的默认值"**：
两处文案必然分叉，而且会出现"改了文件却没生效"这种最难排查的情况。
``run.py`` 启动时先调用 :func:`preload`，把这类问题变成一行清晰的启动错误。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

from liz_bot.runtime_paths import TEXT_FILE_PATH

#: 回复文本文件。随仓库分发、只读（运行时只读它，不改它）。
REPLIES_JSON = os.path.join(TEXT_FILE_PATH, "replies.json")

#: 键 → (值类型, 该模板允许的占位符名)。
#: 类型只有 ``str`` 与 ``list``；``list`` 的元素必须是非空字符串。
#: 占位符为空元组表示该值**不是**模板（出现 ``{`` 会在载入时报错）。
_SCHEMA: dict[str, tuple[type, tuple[str, ...]]] = {
    # ---- 查歌 / 别名查询 ----
    "song.not_found": (str, ()),
    "song.missing": (str, ()),
    "song.data_error": (str, ()),
    "song.bad_params": (str, ()),
    "song.alias_header": (str, ("aliases",)),
    # 命中多个候选（SD / DX 同名）时的消歧追问（见 command_router 的消歧流程）
    "song.choose": (str, ("count", "options")),
    "song.choose_option": (str, ("index", "type", "title", "id")),
    "song.format": (str, ("title", "artist", "id", "master_ds",
                          "master_charter", "rem_ds", "rem_charter")),
    # ---- 指令分发 ----
    "router.unknown_command": (str, ("cmd_name",)),
    # 参数个数校验（见 command_router.COMMANDS 的 min/max_params）
    "router.bad_params": (str, ("usage",)),
    "router.too_many_params": (str, ("usage",)),
    # 多轮补参：参数不够时的追问（见 liz_bot/pending.py）
    # ⚠️ 刻意**不带** {usage} —— 追问只报「还差几个 + 还差哪几个参数」，
    #    不带指令头（用户要求）。需要看完整用法的场景走 router.bad_params。
    #    {params} 取自该指令 help 文案里的 <...> 占位符（见 command_router._param_names），
    #    所以文案里的参数名只有一份，不会漂移。
    "router.ask_params": (str, ("missing", "params")),
    # help 文案里没写 <...> 占位符时的退化文案（正常不会用到，有测试守着）
    "router.ask_params_noparam": (str, ("missing",)),
    # 补参 / 消歧期间**执行失败**（值不对：查不到、难度写错、参数给多了）时的提示。
    # 见 command_router._retry_text —— 失败**不清状态**，让用户重发一次即可。
    # {error} 是原始的失败文案（可能多行，如 judge.no_chart 会列出可用难度），
    # 所以这里只追加一行「重来」的引导，不重复解释原因。
    "router.retry_params": (str, ("error", "params")),
    "router.retry_params_noparam": (str, ("error",)),
    # ---- 舞萌命名空间（`#` 前缀）----
    # 实现尚未接入，所有 `#` 指令统一回这一句
    # （见 command_router.handle_maimai_command）
    "maimai.unparsed": (str, ()),
    # ---- 谱面判定细节（/songdata，见 liz_bot/judge_detail.py）----
    # 难度显示名，下标即难度下标（0=Basic … 4=Re:Master）。
    # ⚠️ 顺序必须与曲库 charts/level/ds 的下标一致，别重排。
    "judge.difficulties": (list, ()),
    "judge.header": (str, ("title", "difficulty", "level", "charter")),
    "judge.scale": (str, ("base_score", "bonus_score")),
    "judge.row_count": (str, ()),
    # 刻意没有 row_cp_p —— cp/p 恒不扣分，不占表行（见 judge_detail 模块文档）
    "judge.row_great": (str, ()),
    "judge.row_good": (str, ()),
    "judge.row_miss": (str, ()),
    "judge.unit": (str, ()),
    "judge.break_title": (str, ("count",)),
    "judge.break_hint": (str, ()),
    "judge.no_break": (str, ()),
    # 表格里「该类型没有音符 / 见另一张表」的占位符
    "judge.placeholder": (str, ()),
    # 一行一个难度（原先用 available_sep 拼成一行，最宽达 79 格必然折行）
    "judge.available_line": (str, ("name", "level")),
    "judge.bad_difficulty": (str, ("value",)),
    "judge.no_chart": (str, ("difficulty", "available")),
    # ---- 估分（/估分，见 liz_bot/score_estimate.py）----
    # 给「目标达成率 + DX 星级」，反推可上传的判定分布。
    "estimate.result": (str, ("target", "actual")),
    # 副标题（曲名单独画，所以与 judge.header 分开一份）
    "estimate.sub": (str, ("difficulty", "level", "charter")),
    "estimate.stars": (str, ("stars", "want", "dx", "max_dx")),
    # {want} 的三种形态（见 score_estimate.stars_summary）：写死的星级 / 不限 /
    # dx理论。分成三个键是为了让「不限」与「dx理论」不必硬拼出「不限★」这种怪句子。
    "estimate.want_star": (str, ("star",)),
    "estimate.want_any": (str, ()),
    "estimate.want_dx_full": (str, ()),
    # 推荐段取不到时补一句 —— 那种情况下 DX 会贴到该星级的边上（见 dx_pick_band），
    # 不说明白用户会以为算错了。{lo}-{hi} 是该星级完整区间，{plo}-{phi} 是推荐段。
    "estimate.dx_out": (str, ("stars", "lo", "hi", "plo", "phi")),
    # combo 等级是「恰好」约束：{combo} 是要求（FC / FC+ / AP / AP+ / 无），
    # {label} 是这份判定分布实际会显示的连击状态（两者必然一致）。
    "estimate.combo_req": (str, ("combo", "label")),
    # **只有 AP+** 会忽略百分比（见 score_estimate.COMBO_PERCENT_IGNORED）：
    # 它只有 101% 一个值，结果行里的「目标」是算法自己取的，必须说明白，
    # 否则用户以为是他要的数。
    # ⚠️ AP **不忽略**（2026-09-27 起它要用百分比选档，挑不到就报不可达）；
    # ⚠️ 两者都只忽略百分比 —— **星级照常生效**（DX 分由「多少普通音符是小 P」决定）。
    "estimate.ignored": (str, ("combo",)),
    "estimate.scale": (str, ("normal", "bonus")),
    # 判定明细表的列头（CP/P/Gr/Gd/Ms 是 ASCII，不进文案）
    "estimate.table_zone": (str, ()),
    "estimate.table_head": (str, ()),
    "estimate.upload_title": (str, ()),
    # {name} 是协议字段名（ASCII，代码里补齐宽度），{value} 是它的值
    "estimate.upload_line": (str, ("name", "value")),
    # 连击状态：PlayComboFlagID 为 0 时只有前一个键可用
    "estimate.combo_none": (str, ()),
    "estimate.combo": (str, ("flag", "label")),
    # gap = 实际达成率 - 目标（恒 ≥ 0，单位 1/10000 %）
    "estimate.gap_zero": (str, ()),
    "estimate.gap_note": (str, ("gap",)),
    # gap 超过 TOLERANCE（0.1%）时**必须显式告警** —— 例如 AP 等级下
    # 普通音符不扣分，达成率只能是 101% - 25k/加成理论分 这一串离散值，
    # 100.0% 根本取不到，只能给最接近的。
    "estimate.gap_warn": (str, ("gap",)),
    # 参数 / 求解失败文案（必须登记进 command_router._FAILURE_KEYS）
    "estimate.bad_percent": (str, ("value",)),
    "estimate.bad_stars": (str, ("value",)),
    "estimate.bad_combo": (str, ("value",)),
    # x小 / x小P 写法（AP 下 break 的小 P 数）
    "estimate.bad_break_p": (str, ("value",)),
    "estimate.combo_conflict": (str, ("combo",)),
    # x小 已经钉死了 DX 分（= 满分 - x），再要求 dx理论 就自相矛盾
    "estimate.dx_conflict": (str, ()),
    "estimate.break_p_note": (str, ("count",)),
    # dx理论 把 DX 钉成满分 ⇒ 全 CP ⇒ 达成率必然 101%，百分比同样失去意义
    "estimate.dx_full_note": (str, ()),
    "estimate.need_percent": (str, ()),
    # AP 的达成率有下界（恒 ≥ 100.75%，见 score_estimate.AP_MIN_PERCENT）——
    # 低于门槛直接说不可达，别默默给一个高得多的数。
    "estimate.ap_unreachable": (str, ("value",)),
    "estimate.too_high": (str, ()),
    "estimate.no_solution": (str, ()),
    "estimate.combo_unreachable": (str, ("combo",)),
    "estimate.empty_chart": (str, ()),
    "estimate.unit": (str, ()),
    # ---- 日常指令 ----
    "daily.help": (str, ()),
    "daily.help_alias": (str, ("aliases",)),
    # /help 图片版的表头（见 liz_bot/help_image.py）。
    # 文字版没有表头 —— 它靠「/用法 — 说明」的行内结构，加了反而更挤。
    "daily.help_usage": (str, ()),
    "daily.help_desc": (str, ()),
    # /help 末尾的舞萌命名空间说明（见 command_router.help_reply）
    "daily.help_maimai": (str, ()),
    "daily.greeting": (str, ()),
    # ---- 别名管理（写入已停用，仅保留提示文案）----
    "alias.add_failed": (str, ()),
    "alias.add_bad_params": (str, ()),
    # ---- 机器人外壳 ----
    "bot.none_reply": (list, ()),
    "bot.not_command": (str, ("content",)),
    "bot.error": (str, ("error",)),
    # ---- 指令表（/help 用）----
    # 每个键对应 command_router.COMMANDS 里的一条指令，值是**整行** help 文案，
    # 形如 ``/song <歌名或别名> — 按歌名或别名查歌``。
    # ⚠️ 必须以 ``/{该指令的规范名}`` 开头 —— command_router.help_reply() 会
    # 逐条断言这一点，防止「改了指令名却忘了改文案」这类静默漂移。
    "commands.help": (str, ()),
    "commands.hello": (str, ()),
    "commands.random": (str, ()),
    "commands.id": (str, ()),
    "commands.songdata": (str, ()),
    # 可选：``/help`` 列表里的**简短写法**。参数多的指令只**缩短参数名**、
    # 不省略参数（免得整行在手机端折行）；完整签名仍在 ``commands.<key>`` 里
    # （补参追问要按它派生参数名，报错也拿它当 usage）。
    "commands.songdata_brief": (str, ()),
    "commands.estimate": (str, ()),
    "commands.estimate_brief": (str, ()),
    "commands.bm": (str, ()),
    "commands.name": (str, ()),
    "commands.song": (str, ()),
    "commands.alias_query": (str, ()),
    "commands.alias_add": (str, ()),
}


class RepliesError(RuntimeError):
    """回复文本文件缺失 / 格式错误。消息里带修复提示，可直接打印给用户。"""


_state: dict[str, Any] | None = None
_mtime: int | None = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 载入与校验
# ---------------------------------------------------------------------------

def _flatten(raw: Any, prefix: str = "") -> dict[str, Any]:
    """把两层 JSON 拍平成 ``{"song.not_found": ...}``，便于按点号路径取值。"""
    flat: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return flat
    for key, value in raw.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, prefix=f"{path}."))
        else:
            flat[path] = value
    return flat


def _validate(flat: dict[str, Any]) -> None:
    """校验必需键、类型与占位符。任何问题都抛 :class:`RepliesError`。"""
    problems: list[str] = []

    for key, (kind, placeholders) in _SCHEMA.items():
        if key not in flat:
            problems.append(f"缺少键：{key}")
            continue

        value = flat[key]
        if kind is str:
            if not isinstance(value, str):
                problems.append(f"{key} 应为字符串，实际是 {type(value).__name__}")
                continue
            if not value:
                problems.append(f"{key} 是空字符串")
                continue
            # 试渲染：占位符写错、花括号不配对都在这里暴露
            try:
                value.format(**{name: "x" for name in placeholders})
            except (KeyError, IndexError, ValueError) as exc:
                problems.append(
                    f"{key} 模板有问题（{type(exc).__name__}: {exc}）；"
                    f"该键允许的占位符只有 {list(placeholders) or '无'}"
                    f"（要输出花括号本身请写 {{{{ 和 }}}}）"
                )
        else:  # list
            if not isinstance(value, list):
                problems.append(f"{key} 应为数组，实际是 {type(value).__name__}")
                continue
            if not value:
                problems.append(f"{key} 是空数组")
                continue
            bad = [v for v in value if not isinstance(v, str) or not v]
            if bad:
                problems.append(f"{key} 含非字符串或空元素：{bad[:3]}")
                continue

    unknown = sorted(set(flat) - set(_SCHEMA))
    if unknown:
        # 多余键通常只是改名后的残留，不影响运行，提示即可
        print(f"[replies] 提示：{REPLIES_JSON} 里有未使用的键：{unknown}",
              file=sys.stderr)

    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        raise RepliesError(
            f"回复文本文件校验未通过：{REPLIES_JSON}\n{detail}\n"
            f"（格式说明见 liz_bot/replies.py 的模块文档）"
        )


def _read() -> dict[str, Any]:
    """读盘 + 校验，返回拍平后的键值表。"""
    try:
        with open(REPLIES_JSON, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raise RepliesError(
            f"回复文本文件不存在：{REPLIES_JSON}\n"
            f"该文件随仓库分发，请检查是否被误删；"
            f"或它被 .dockerignore 排除了（会导致镜像里没有这个文件）。"
        ) from None
    except OSError as exc:
        raise RepliesError(
            f"无法读取回复文本文件：{REPLIES_JSON}（{exc}）"
        ) from None
    except json.JSONDecodeError as exc:
        raise RepliesError(
            f"回复文本文件不是合法 JSON：{REPLIES_JSON}\n"
            f"  {exc}\n"
            f"  常见原因：多/少了一个逗号、漏了引号、"
            f"用了单引号或中文引号（JSON 只认英文双引号）"
        ) from None

    if not isinstance(raw, dict):
        raise RepliesError(
            f"回复文本文件顶层应为对象（{{...}}），实际是 {type(raw).__name__}："
            f"{REPLIES_JSON}"
        )

    flat = _flatten(raw)
    _validate(flat)
    return flat


def _load(force: bool = False) -> dict[str, Any]:
    """按 mtime 决定是否重新读盘。

    热更新就靠这里：mtime 未变直接返回缓存，变了才重读。
    文件被删除时 ``os.stat`` 失败 → mtime 为 ``None`` → 与缓存的 mtime 不等
    → 走重读路径 → 抛出清晰的 :class:`RepliesError`（而不是继续用旧文案）。
    """
    global _state, _mtime

    try:
        mtime: int | None = os.stat(REPLIES_JSON).st_mtime_ns
    except OSError:
        mtime = None

    if _state is not None and not force and mtime == _mtime:
        return _state

    with _lock:
        if _state is not None and not force and mtime == _mtime:
            return _state
        state = _read()
        _state = state
        _mtime = mtime
        return state


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def get(key: str) -> Any:
    """按点号路径取原始值（字符串或字符串数组）。

    :raises RepliesError: 键不存在
    """
    try:
        return _load()[key]
    except KeyError:
        raise RepliesError(
            f"回复文本里没有这个键：{key}\n"
            f"  可用键见 liz_bot/replies.py 的 _SCHEMA（或直接看 {REPLIES_JSON}）"
        ) from None


def text(key: str, **fields: Any) -> str:
    """取字符串值，必要时按占位符渲染。

    :param key: 点号路径，如 ``"song.not_found"``
    :param fields: 模板占位符，如 ``text("song.format", title="...")``
    :raises RepliesError: 键不存在、类型不对、或占位符不匹配
    """
    value = get(key)
    if not isinstance(value, str):
        raise RepliesError(
            f"{key} 不是字符串（实际是 {type(value).__name__}），"
            f"数组类型的值请用 replies.get()"
        )

    allowed = _SCHEMA.get(key, (str, ()))[1]
    if allowed and not fields:
        raise RepliesError(
            f"{key} 是模板，必须提供占位符：{list(allowed)}"
        )
    if not allowed and fields:
        raise RepliesError(
            f"{key} 不是模板，不接受占位符：{sorted(fields)}"
        )
    if not fields:
        return value

    try:
        return value.format(**fields)
    except (KeyError, IndexError, ValueError) as exc:
        raise RepliesError(
            f"{key} 渲染失败（{type(exc).__name__}: {exc}）；"
            f"该键允许的占位符：{list(allowed)}，实际传入：{sorted(fields)}"
        ) from None


def preload() -> None:
    """启动时显式载入一次，让文件问题变成**一行清晰的启动错误**。

    真正的目的是尽早失败：文案文件缺失/损坏时，宁可在启动日志里立刻看到，
    也不要等到群里第一条消息才炸。
    """
    _load(force=True)


def reload() -> None:
    """丢弃缓存，下次取值重新读盘。

    通常**不必手动调用** —— 改文件后 mtime 变化会自动重载。
    保留它是为了在 mtime 精度不足的文件系统上兜底。
    """
    global _state, _mtime
    with _lock:
        _state = None
        _mtime = None


def keys() -> list[str]:
    """列出全部键（调试用）。"""
    return sorted(_load())
