"""查歌功能 —— 按 ID / 歌名 / 别名检索曲库，并格式化为回复文本。

数据源
------
本模块读**两个**只读数据文件，用曲目 ID 关联（DX 为 ``id + 10000``）::

    liz_bot/divingfish_songs/music_data.json   水鱼曲库（曲名 / 定数 / 谱师）
    liz_bot/yuzuchan_aliases/aliases.json      柚子别名库（每首曲的别名）

分别由 ``_tools/fetch_music_data.py`` 与 ``_tools/fetch_aliases.py`` 拉取，
都随镜像分发。**水鱼不提供歌曲别名**（它的 ``alias`` 只出现在"查询参数
别名"里），别名由柚子单独维护，且是玩家众包投票产生的。

水鱼单首乐曲的结构::

    {
      "id":    "8",                       # 曲目 ID（字符串形式）
      "title": "True Love Song",
      "type":  "SD",                      # "SD" / "DX"
      "ds":    [5.0, 7.2, 10.2, 12.4],    # 定数，下标与 charts 对齐
      "level": ["5", "7", "10", "12"],    # 显示等级，可能带 "+"，宴会场带 "?"
      "cids":  [1, 2, 3, 4],              # 谱面 ID
      "charts": [{"notes": [...], "charter": "-"}, ...],
      "basic_info": {
        "title": "...", "artist": "...", "genre": "舞萌",
        "bpm": 150, "release_date": "", "from": "maimai", "is_new": false
      }
    }

柚子别名库的结构（落盘内容就是上游原始数组）::

    [
      {"song_id": 8, "name": "True Love Song", "is_votable": true,
       "alias": ["true love song", "会员制餐厅", "真的爱情歌", ...]},
      ...
    ]

与旧曲库（maimaiDX-songs）的字段对照
------------------------------------
=================  ==========================  ==========================
含义               旧字段                      新字段
=================  ==========================  ==========================
曲名               ``name``                    ``title``
曲师               ``artist``                  ``basic_info.artist``
类型               ``type``（int 0/1）          ``type``（"SD"/"DX"）
定数               ``charts[i].level``         ``ds[i]``
显示等级            ——                         ``level[i]``
追加版本           ``version``（int 100）       ``basic_info.from``（"maimai"）
追加日期           ``date``                    ``basic_info.release_date``
谱师 / 音符数       ``charts[i].charter/notes``  同名保留
别名               ``alias[]``（旧库内嵌）      ``alias[]``（柚子库，按 id 关联）
=================  ==========================  ==========================

三个必须注意的差异
------------------
1. **``charts`` 长度不固定**，实测为 1 / 2 / 4 / 5 四种。宴会场曲目
   （``id ≥ 100000``）只有 1 张谱面，且 ``level`` 带 ``?`` 后缀（``"12?"``）。
   旧实现固定读 ``charts[3]``，对这类曲目会抛 IndexError，又被
   ``song_reply`` 吞成「没找到」—— 等于**宴会场曲目在旧代码里查不出来**。
   本实现按实际长度降级为 :data:`MISSING`（``" -"``，与旧实现回退分支的
   写法保持一致，含前导空格）。
2. **定数一律是 float**。旧曲库把整数定数存成 int（``13`` 输出 ``"13"``），
   水鱼一律存 float（``13.0``），直接 ``str()`` 会变成 ``"13.0"``。
   这里用 :func:`_fmt_ds` 归一化，保证回复文本与重构前逐字一致。
3. **别名改用 ``song_id`` 关联**。旧库以**曲名**为主键
   （``{"name": ..., "alias": [...]}``），而旧库里的曲名大小写常与曲库
   不一致（旧库里是 ``7thsense``、曲库是 ``7thSense``），
   严格相等匹配因此**静默失配**，这是"别名数据大量缺失"的根因之一。
   柚子库直接给 ``song_id``，关联可靠。

主要对外接口：
    select_song(select_data, select_type)  低层检索，返回原始结构
    query_by_id / query_by_name / query_by_alias / query_any
    song_reply / songdata_reply / alias_reply  直接产出可回复的字符串

回复文案
--------
本模块**不再硬编码任何用户可见文案** —— 未命中文案、缺难度占位符、别名列表
标题、查歌排版模板全部来自 ``liz_bot/texts/replies.json``（经 :mod:`liz_bot.replies`
读取，**改文件即时生效、不必重启**）。查歌排版是其中的 ``song.format`` 键，
可用占位符见 :data:`liz_bot.replies._SCHEMA`。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from liz_bot import replies
from liz_bot.song_paths import ALIASES_JSON, MUSIC_DATA_JSON

# ---------------------------------------------------------------------------
# 检索类型常量
# ---------------------------------------------------------------------------

BY_ID = 0
BY_NAME = 1
BY_ALIAS = 2
BY_ANY = 3

# 难度下标：0=Basic 1=Advanced 2=Expert 3=Master 4=Re:MASTER
IDX_MASTER = 3
IDX_REMASTER = 4

# 宴会场曲目的 ID 下界（与旧实现一致）
UTAGE_ID_MIN = 100000

#: 历史常量名 → 回复文本键。文案本身在 ``liz_bot/texts/replies.json``。
#:
#: 这里用模块级 ``__getattr__``（PEP 562）而不是模块级赋值，是为了同时满足两件事：
#:   1. **调用方写法不变** —— ``song_query.NOT_FOUND`` / ``song_query.MISSING``
#:      仍可用（``command_router`` 与部署自检脚本都在用）；
#:   2. **改文案立刻生效** —— 若写成模块级赋值，值会在 import 时被冻结，
#:      改了文件也不变，与 replies 的热更新语义自相矛盾。
#:
#: 注意：模块**内部**的裸名 ``NOT_FOUND`` 不会触发 ``__getattr__``
#: （那只对模块的属性访问生效），所以本模块内部一律直接调 ``replies.text(...)``。
_LEGACY_ALIASES = {
    "NOT_FOUND": "song.not_found",
    "MISSING": "song.missing",
}


def __getattr__(name: str) -> Any:
    """让 ``song_query.NOT_FOUND`` 这类旧常量名继续可用（且始终返回最新文案）。"""
    key = _LEGACY_ALIASES.get(name)
    if key is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return replies.text(key)


# ---------------------------------------------------------------------------
# 曲库载入
# ---------------------------------------------------------------------------

_cache: dict[str, Any] | None = None
_cache_lock = threading.Lock()


def _load() -> dict[str, Any]:
    """载入曲库并建立索引，进程内只做一次。

    为什么全量载入而不是流式
    ------------------------
    旧实现用 ijson 流式遍历文件，**每次查歌都要把 1MB 的 JSON 重新扫一遍**。
    改为全量载入 + 建索引后，检索由「遍历整个文件」变成「查字典」。

    这是**拿内存换时间**，实测（Python 3.10 / Windows，含 `qqgroupbot` 导入
    的 38MB 基线，故只比较增量）::

        ==================  ============  ============
        指标                旧（ijson）   新（常驻索引）
        ==================  ============  ============
        曲库占用（常驻）      +3.7 MB       +5.3 MB
        峰值（含临时对象）    +4.4 MB       +6.9 MB
        单次 BY_ID           21.3 ms       9.7 ms（首次）
        单次 BY_NAME         24.3 ms       ~0 ms
        单次 BY_ANY          40.5 ms       ~0 ms
        连查 10 次 BY_ID    240.5 ms       ~0 ms
        ==================  ============  ============

    即**多花约 1.6 MB 常驻内存，换掉每次查歌 20–40 ms 的重复扫盘**。
    绝对量都很小（进程总计约 43 MB），对容器内存限制无压力。

    别名库（约 250 KB）同理一次性载入并建反向索引。

    :return: ``{"songs": [...], "by_id": {...}, "by_title": {...}}``
    :raises FileNotFoundError: 曲库文件缺失（提示如何生成）
    """
    global _cache
    if _cache is not None:
        return _cache

    with _cache_lock:
        # 双检：并发首次调用时只解析一次
        if _cache is not None:
            return _cache

        if not os.path.exists(MUSIC_DATA_JSON):
            raise FileNotFoundError(
                f"曲库文件不存在：{MUSIC_DATA_JSON}\n"
                f"请先运行：python _tools/fetch_music_data.py"
            )

        with open(MUSIC_DATA_JSON, "r", encoding="utf-8") as fh:
            songs = json.load(fh)

        if not isinstance(songs, list):
            raise ValueError(f"曲库顶层应为数组，实际是 {type(songs).__name__}")

        by_id: dict[int, dict] = {}
        by_title: dict[str, list[dict]] = {}
        for song in songs:
            if not isinstance(song, dict):
                continue
            try:
                by_id[int(song["id"])] = song
            except (KeyError, TypeError, ValueError):
                pass
            title = song.get("title")
            if isinstance(title, str):
                # 同名不同版本（SD / DX）都保留，与旧实现「返回全部命中」一致
                by_title.setdefault(title, []).append(song)

        _cache = {"songs": songs, "by_id": by_id, "by_title": by_title}
        return _cache


# ---------------------------------------------------------------------------
# 别名库载入
# ---------------------------------------------------------------------------

_alias_cache: dict[str, Any] | None = None
_alias_lock = threading.Lock()


def _normalize_alias(text: str) -> str:
    """别名的比较形式：去首尾空白 + 转小写。

    柚子以**大小写不敏感**的方式匹配别名（实测 ``True Love Song`` 与
    ``TRUE LOVE SONG`` 都能命中，而 ``真`` 命中不了 ``真爱`` —— 是精确
    匹配而非子串）。这里保持一致。
    """
    return text.strip().lower()


def _load_aliases() -> dict[str, Any]:
    """载入别名库并建索引，进程内只做一次。

    索引结构::

        by_alias: {"真爱": [8], "⑨": [199, 302, 665, 769, 10302, 10665], ...}
                  # 小写化别名 -> song_id 列表（检索用，保留先后顺序）
        by_id:    {8: ["true love song", "会员制餐厅", ...], ...}
                  # song_id -> 别名列表（展示用，保留上游原始大小写）

    **文件缺失时返回空索引而不是抛异常** —— 别名只是增强功能，
    没有它按 ID / 歌名查歌仍应照常可用。

    :return: ``{"records": [...], "by_alias": {...}, "by_id": {...}}``
    """
    global _alias_cache
    if _alias_cache is not None:
        return _alias_cache

    with _alias_lock:
        if _alias_cache is not None:
            return _alias_cache

        records: list = []
        by_alias: dict[str, list[int]] = {}
        by_id: dict[int, list[str]] = {}

        if os.path.exists(ALIASES_JSON):
            try:
                with open(ALIASES_JSON, "r", encoding="utf-8") as fh:
                    records = json.load(fh)
            except (OSError, json.JSONDecodeError):
                records = []
            if not isinstance(records, list):
                records = []

        for rec in records:
            if not isinstance(rec, dict):
                continue
            song_id = _as_int(rec.get("song_id"))
            if song_id < 0:
                continue
            raw = rec.get("alias")
            if not isinstance(raw, list):
                continue
            cleaned = [a for a in raw if isinstance(a, str) and a.strip()]
            if not cleaned:
                continue
            by_id[song_id] = cleaned
            for alias in cleaned:
                bucket = by_alias.setdefault(_normalize_alias(alias), [])
                if song_id not in bucket:
                    bucket.append(song_id)

        _alias_cache = {"records": records, "by_alias": by_alias, "by_id": by_id}
        return _alias_cache


def _aliases_for(song_id: int) -> list[str]:
    """取某首曲的别名列表；没有则返回空列表。"""
    if song_id < 0:
        return []
    return list(_load_aliases()["by_id"].get(song_id, []))


def clear_cache() -> None:
    """丢弃已载入的曲库与别名库，下次查询重新读盘。供测试与热更新使用。"""
    global _cache, _alias_cache
    with _cache_lock:
        _cache = None
    with _alias_lock:
        _alias_cache = None


def library_stats() -> dict[str, int]:
    """曲库与别名库概况，供启动日志 / 健康检查展示。"""
    data = _load()
    songs = data["songs"]
    alias_index = _load_aliases()
    return {
        "songs": len(songs),
        "charts": sum(len(s.get("charts") or []) for s in songs),
        "dx": sum(1 for s in songs if s.get("type") == "DX"),
        "sd": sum(1 for s in songs if s.get("type") == "SD"),
        "utage": sum(1 for s in songs if _as_int(s.get("id")) >= UTAGE_ID_MIN),
        "aliases": sum(len(v) for v in alias_index["by_id"].values()),
        "alias_keys": len(alias_index["by_alias"]),
    }


def _as_int(value: Any) -> int:
    """尽力把曲目 ID 转成 int，失败返回 -1（用于分类统计，不参与检索）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------

def _wrap(song: dict) -> dict:
    """统一的返回单元。

    ``aliases`` 是该曲在柚子别名库中的别名列表（可能为空：别名库文件缺失，
    或该曲在库里没有条目）。旧实现返回的是别名库里**同名**条目的别名 ——
    旧库以曲名为主键，而大小写不一致时会静默失配；现在直接按 ``song_id``
    关联，更可靠。
    """
    return {"song": song, "aliases": _aliases_for(_as_int(song.get("id")))}


def _songs_by_alias(data: dict, keyword: str) -> list[dict]:
    """按别名检索：**大小写不敏感的精确匹配**（与柚子服务端一致）。

    一个别名可能对应多首曲 —— 实测 518 个别名被多首共享，最多的 ``⑨``
    有 6 首。因此返回列表，调用方按惯例取第一首。
    """
    if not isinstance(keyword, str):
        return []
    song_ids = _load_aliases()["by_alias"].get(_normalize_alias(keyword))
    if not song_ids:
        return []
    by_id = data["by_id"]
    return [_wrap(by_id[sid]) for sid in song_ids if sid in by_id]


def select_song(select_data: str, select_type: int):
    """检索曲库。

    :param select_data: 检索关键词
    :param select_type: 0 按 ID，1 按歌名，2 按别名，3 混合（歌名 → ID → 别名）
    :return: ``[{"song": 完整乐曲对象, "aliases": [别名, ...]}, ...]``，
             未命中返回空列表。乐曲对象即曲库中的原始 dict，字段见模块说明。
    """
    if not isinstance(select_data, str):
        return []

    data = _load()

    if select_type == BY_ID:
        song = data["by_id"].get(_as_int(select_data))
        return [_wrap(song)] if song else []

    if select_type == BY_NAME:
        return [_wrap(s) for s in data["by_title"].get(select_data, [])]

    if select_type == BY_ALIAS:
        return _songs_by_alias(data, select_data)

    if select_type == BY_ANY:
        # 顺序刻意是「精确歌名 → 纯数字当 ID → 别名」，别名放最后。
        #
        # 别名的键空间与曲目 ID 有重叠：song_id=302 的别名就是 "9"，
        # 而 song_id=9 是 Color My World。若让别名优先，`/song 9` 就会被
        # 抢到 302 去 —— 而用户输纯数字时几乎总是想要 ID。
        # 别名只在歌名与 ID 都对不上时才兜底，`/song <别名>` 依旧可用。
        hits = [_wrap(s) for s in data["by_title"].get(select_data, [])]
        if hits:
            return hits
        song = data["by_id"].get(_as_int(select_data))
        if song:
            return [_wrap(song)]
        return _songs_by_alias(data, select_data)

    return []


# ---------------------------------------------------------------------------
# 指令层封装
# ---------------------------------------------------------------------------

def query_by_id(keyword: str):
    """按曲目 ID 检索。"""
    return select_song(select_data=keyword, select_type=BY_ID)


def query_by_name(keyword: str):
    """按歌名精确检索。"""
    return select_song(select_data=keyword, select_type=BY_NAME)


def query_by_alias(keyword: str):
    """按别名检索（大小写不敏感的精确匹配）。"""
    return select_song(select_data=keyword, select_type=BY_ALIAS)


def query_any(keyword: str):
    """混合检索：歌名 → ID → 别名。"""
    return select_song(select_data=keyword, select_type=BY_ANY)


# ---------------------------------------------------------------------------
# 文本格式化
# ---------------------------------------------------------------------------

def _fmt_ds(value: Any) -> str:
    """定数转字符串，整数值去掉小数尾巴（``13.0`` → ``"13"``）。

    旧曲库把整数定数存成 int、带小数存成 float，输出因此是 ``13`` / ``13.1``；
    水鱼曲库一律是 float，直接 ``str()`` 会得到 ``13.0``。这里按旧观感归一化，
    使回复文本与重构前逐字一致。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return replies.text("song.missing")
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


def _chart_fields(song_data: dict, index: int) -> tuple[str, str]:
    """取某难度的 ``(定数, 谱师)``，该难度不存在时降级为 ``song.missing``。

    宴会场曲目只有 1 张谱面，Master / Re:MASTER 都不存在 —— 旧实现固定索引
    ``charts[3]``，在这类曲目上会抛 IndexError。
    """
    missing = replies.text("song.missing")
    charts = song_data.get("charts") or []
    if index >= len(charts):
        return missing, missing
    chart = charts[index] if isinstance(charts[index], dict) else {}
    ds_list = song_data.get("ds") or []
    ds_value = ds_list[index] if index < len(ds_list) else None
    charter = chart.get("charter")
    return _fmt_ds(ds_value), (charter if charter else missing)


def format_song(song_data: dict) -> str:
    """把单首乐曲对象格式化为回复文本。

    排版**不再硬编码**：模板来自 ``replies.json`` 的 ``song.format`` 键，
    可用占位符见 :data:`liz_bot.replies._SCHEMA`。

    默认模板与原实现**逐字一致**（含行首 ``\\n``、每行末尾的空格，以及缺难度时
    ``" -"`` 的前导空格）—— 想调排版改模板即可，不必碰代码。
    """
    basic = song_data.get("basic_info") or {}
    master_ds, master_charter = _chart_fields(song_data, IDX_MASTER)
    rem_ds, rem_charter = _chart_fields(song_data, IDX_REMASTER)
    return replies.text(
        "song.format",
        title=song_data.get("title"),
        artist=basic.get("artist"),
        id=song_data.get("id"),
        master_ds=master_ds,
        master_charter=master_charter,
        rem_ds=rem_ds,
        rem_charter=rem_charter,
    )


def song_reply(keyword: str, select_type: int) -> str:
    """查歌指令的统一回复入口。

    :param keyword: 检索关键词
    :param select_type: 0/1/2/3 见 select_song
    :return: 格式化后的回复文本，未命中返回 ``song.not_found`` 的文案
    """
    try:
        song_data = select_song(select_data=keyword, select_type=select_type)
    except IndexError:
        return replies.text("song.not_found")
    if not song_data:
        return replies.text("song.not_found")
    return format_song(song_data[0]['song'])


def songdata_reply(keyword: str) -> str:
    """原样输出检索到的完整结构（调试用）。"""
    try:
        return str(select_song(select_data=keyword, select_type=BY_ID))
    except IndexError:
        return replies.text("song.data_error")


def alias_reply(keyword: str) -> str:
    """查询某首歌的全部别名。

    文本格式取 ``replies.json`` 的 ``song.alias_header`` 键（默认
    ``歌曲有以下别名：{aliases}``，别名列表按 Python 列表原样输出），
    数据源为柚子别名库。

    曲目存在但别名库里没有它的条目时返回 ``song.not_found`` 的文案 ——
    与旧实现"在 alias.json 里找不到同名条目就返回 NOT_FOUND"的行为一致。
    """
    try:
        song_data = select_song(select_data=keyword, select_type=BY_ANY)
        if not song_data:
            return replies.text("song.not_found")
        aliases = song_data[0]["aliases"]
        if not aliases:
            return replies.text("song.not_found")
        return replies.text("song.alias_header", aliases=aliases)
    except (IndexError, TypeError):
        return replies.text("song.bad_params")
