"""成绩容器。

移植自 Lionheart ``src/containers/Score.ts``。

TS 版用 ``Proxy`` 实现 ``score[MusicDifficultyID.Basic]`` 这种动态属性访问；
Python 没有 Proxy，改用 ``__getitem__`` / ``__setitem__``，
语义保持一致::

    score = score_set.get(1145)
    entry = score[MusicDifficultyID.Master]
    entry.achievement = 1005000          # 自动登记为待上传的修改
    score[MusicDifficultyID.Expert] = {"achievement": 990000}

核心机制是**脏跟踪**：只有被改动过的成绩才会出现在 :meth:`ScoreSet.export`
的结果里，供 ``UpsertUserAllApi`` 使用。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Iterator, Mapping

from ..typings.base import (
    MusicClearRankID,
    MusicDifficultyID,
    PlayComboFlagID,
    PlaySyncFlagID,
)

__all__ = [
    "ScoreEntry",
    "Score",
    "ScoreSet",
    "convert_achievement_to_score_rank",
    "DIFFICULTY_LEVELS",
]

#: 合法难度集合。Utage 是 10，与 0..4 不连续，故显式列出而非用 range。
DIFFICULTY_LEVELS: tuple[int, ...] = (
    MusicDifficultyID.Basic,
    MusicDifficultyID.Advanced,
    MusicDifficultyID.Expert,
    MusicDifficultyID.Master,
    MusicDifficultyID.ReMaster,
    MusicDifficultyID.Utage,
)

#: 会触发脏跟踪的字段。改动这些之外的属性不会登记（与 Lionheart 一致）。
_WRITABLE_FIELDS = frozenset(
    {
        "play_count",
        "achievement",
        "combo_status",
        "sync_status",
        "deluxscore_max",
        "ext_num1",
    }
)

_SLOTS = (
    "level",
    "play_count",
    "achievement",
    "combo_status",
    "sync_status",
    "deluxscore_max",
    "ext_num1",
    "_on_change",
)


def convert_achievement_to_score_rank(achievement: int, utage: bool = False) -> int:
    """把达成率换算为评级。

    边界表取自 Lionheart，Utage 难度下边界翻倍。
    若达成率超出全部边界（例如 > 1010000）则返回 ``-1``，与 Lionheart 行为一致。

    ::

        0 ~ 499999        D        900000 ~ 939999   AA
        500000 ~ 599999   C        940000 ~ 969999   AAA
        600000 ~ 699999   B        970000 ~ 979999   S
        700000 ~ 749999   BB       980000 ~ 989999   S+
        750000 ~ 799999   BBB      990000 ~ 994999   SS
        800000 ~ 899999   A        995000 ~ 999999   SS+
                                  1000000 ~ 1004999  SSS
                                  1005000 ~ 1010000  SSS+
    """
    ranks = (
        MusicClearRankID.Rank_D,
        MusicClearRankID.Rank_C,
        MusicClearRankID.Rank_B,
        MusicClearRankID.Rank_BB,
        MusicClearRankID.Rank_BBB,
        MusicClearRankID.Rank_A,
        MusicClearRankID.Rank_AA,
        MusicClearRankID.Rank_AAA,
        MusicClearRankID.Rank_S,
        MusicClearRankID.Rank_SP,
        MusicClearRankID.Rank_SS,
        MusicClearRankID.Rank_SSP,
        MusicClearRankID.Rank_SSS,
    )
    borders = (
        499999,
        599999,
        699999,
        749999,
        799999,
        899999,
        939999,
        969999,
        979999,
        989999,
        994999,
        999999,
        1004999,
    )

    rank = -1
    multiplier = int(bool(utage)) + 1
    for border, candidate in zip(borders, ranks):
        if achievement <= border * multiplier:
            rank = candidate
            break

    if rank == -1 and 1005000 <= achievement <= 1010000:
        rank = MusicClearRankID.Rank_SSSP
    return rank


class ScoreEntry:
    """单个难度下的成绩。

    ``score_rank`` 由 ``achievement`` 推导，只读。
    修改 ``play_count`` / ``achievement`` 等字段时，若该条目已被
    :class:`ScoreSet` 接管，会自动登记为待上传变更。
    """

    __slots__ = _SLOTS

    def __init__(
        self,
        level: int = MusicDifficultyID.Basic,
        play_count: int = 0,
        achievement: int = 0,
        combo_status: int = PlayComboFlagID.None_,
        sync_status: int = PlaySyncFlagID.None_,
        deluxscore_max: int = 0,
        ext_num1: int = 0,
    ) -> None:
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "play_count", play_count)
        object.__setattr__(self, "achievement", achievement)
        object.__setattr__(self, "combo_status", combo_status)
        object.__setattr__(self, "sync_status", sync_status)
        object.__setattr__(self, "deluxscore_max", deluxscore_max)
        object.__setattr__(self, "ext_num1", ext_num1)
        object.__setattr__(self, "_on_change", None)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        if name in _WRITABLE_FIELDS:
            callback = getattr(self, "_on_change", None)
            if callback is not None:
                callback()

    @property
    def score_rank(self) -> int:
        """评级。Utage 难度且达成率超过 1010000 时按 Utage 边界计算。"""
        return convert_achievement_to_score_rank(
            self.achievement,
            self.achievement > 1010000 and self.level == MusicDifficultyID.Utage,
        )

    @classmethod
    def from_raw(cls, level: int, raw: Mapping[str, Any]) -> "ScoreEntry":
        """从接口返回的原始字段构造，缺失字段按 0 处理（与 Lionheart 一致）。"""

        def num(key: str, default: int = 0) -> int:
            value = raw.get(key)
            return default if value is None else int(value)

        return cls(
            level=int(level),
            play_count=num("playCount"),
            achievement=num("achievement"),
            combo_status=num("comboStatus", PlayComboFlagID.None_),
            sync_status=num("syncStatus", PlaySyncFlagID.None_),
            deluxscore_max=num("deluxscoreMax"),
            ext_num1=num("extNum1"),
        )

    def to_music_detail(self, music_id: int) -> dict[str, Any]:
        """导出为 ``UserMusicDetail`` 形态。"""
        return {
            "musicId": music_id,
            "level": self.level,
            "playCount": self.play_count,
            "achievement": self.achievement,
            "comboStatus": self.combo_status,
            "syncStatus": self.sync_status,
            "deluxscoreMax": self.deluxscore_max,
            "scoreRank": self.score_rank,
            "extNum1": self.ext_num1,
        }

    def __repr__(self) -> str:
        name = MusicDifficultyID(self.level).name
        return (
            f"ScoreEntry({name}, achievement={self.achievement}, "
            f"playCount={self.play_count}, rank={self.score_rank})"
        )


class Score:
    """单曲成绩，按难度索引。对应 Lionheart 里被 ``Proxy`` 包装的 ``Score``。"""

    __slots__ = ("_entries",)

    def __init__(self, entries: dict[int, ScoreEntry] | None = None) -> None:
        self._entries: dict[int, ScoreEntry] = entries if entries is not None else {}

    def __getitem__(self, level: int) -> ScoreEntry | None:
        if level not in DIFFICULTY_LEVELS:
            return None
        return self._entries.get(level)

    def __setitem__(self, level: int, value: ScoreEntry | Mapping[str, Any]) -> None:
        if level not in DIFFICULTY_LEVELS:
            raise KeyError(f"非法难度：{level}")
        entry = (
            value
            if isinstance(value, ScoreEntry)
            else ScoreEntry.from_raw(level, value)
        )
        entry.level = level
        self._entries[level] = entry

    def __contains__(self, level: object) -> bool:
        return level in self._entries

    def __iter__(self) -> Iterator[int]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        filled = ", ".join(
            f"{MusicDifficultyID(level).name}={entry.achievement}"
            for level, entry in sorted(self._entries.items())
        )
        return f"Score({filled})"


class _ModifiedScore:
    """一条待上传的成绩变更。"""

    __slots__ = ("music_id", "level", "entry", "is_new")

    def __init__(self, music_id: int, level: int, entry: ScoreEntry, is_new: bool):
        self.music_id = music_id
        self.level = level
        self.entry = entry
        self.is_new = is_new

    def to_export(self) -> dict[str, Any]:
        return {
            "value": self.entry.to_music_detail(self.music_id),
            "isNew": self.is_new,
        }


class ScoreSet:
    """一批成绩，按 ``musicId`` 索引。

    移植自 Lionheart ``ScoreSet``。只记录被改动过的条目，
    :meth:`export` 返回的即是需要上传的增量。
    """

    def __init__(self, data: Iterable[Mapping[str, Any]] = ()) -> None:
        self._score: dict[int, dict[int, ScoreEntry]] = {}
        #: (musicId, level) -> 变更记录。用 dict 保证插入顺序，
        #: 对应 Lionheart 里对 ``_modifiedScore`` 数组的 find / findIndex。
        self._modified: dict[tuple[int, int], _ModifiedScore] = {}

        for detail in data:
            music_id = int(detail["musicId"])
            level = int(detail["level"])
            bucket = self._score.setdefault(music_id, {})
            bucket[level] = ScoreEntry.from_raw(level, detail)

    # ---- 构造辅助 -------------------------------------------------------
    @classmethod
    def resettable(cls, data: Iterable[Mapping[str, Any]]):
        """返回 ``(score_set, reset)``；调用 ``reset()`` 清空变更记录。"""
        score_set = cls(data)
        return score_set, score_set.reset

    def reset(self) -> None:
        """丢弃全部待上传变更（已加载的成绩本身保留）。"""
        self._modified.clear()

    # ---- 读取 -----------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self._score)

    def get(self, music_id: int) -> Score:
        """取得某首歌的成绩视图。不存在的歌会按需创建空壳（与 Lionheart 一致）。"""
        bucket = self._score.setdefault(music_id, {})
        return _ScoreView(self, music_id, bucket)

    def keys(self):
        return self._score.keys()

    def values(self) -> Iterator[Score]:
        return (self.get(music_id) for music_id in self._score)

    def items(self):
        return ((music_id, self.get(music_id)) for music_id in self._score)

    def __iter__(self) -> Iterator[int]:
        return iter(self._score)

    def __len__(self) -> int:
        return len(self._score)

    def __contains__(self, music_id: object) -> bool:
        return music_id in self._score

    # ---- 写入 -----------------------------------------------------------
    def set(self, music_id: int, values: Mapping[str, Any]) -> None:
        """批量写入某首歌的多个难度，键为难度 ID。"""
        score = self.get(music_id)
        for level, entry in values.items():
            score[int(level)] = entry

    # ---- 导出 -----------------------------------------------------------
    def export(self) -> list[dict[str, Any]]:
        """导出全部待上传的成绩变更。"""
        return [item.to_export() for item in self._modified.values()]

    @property
    def modified_count(self) -> int:
        """待上传的变更条数。"""
        return len(self._modified)

    # ---- 内部：登记变更 --------------------------------------------------
    def _track(
        self, music_id: int, level: int, entry: ScoreEntry, is_new: bool
    ) -> None:
        key = (music_id, level)
        existing = self._modified.get(key)
        if existing is not None:
            existing.entry = entry
            existing.is_new = existing.is_new or is_new
        else:
            self._modified[key] = _ModifiedScore(
                music_id=music_id, level=level, entry=entry, is_new=is_new
            )


class _ScoreView(Score):
    """:class:`Score` 的写入感知版本，由 :class:`ScoreSet` 返回。

    返回的是**存储中的同一个** :class:`ScoreEntry` 对象（而非副本），
    因此字段修改会真正落到数据上，同时通过 ``_on_change`` 回调登记变更。
    """

    __slots__ = ("_owner", "_music_id")

    def __init__(
        self, owner: ScoreSet, music_id: int, entries: dict[int, ScoreEntry]
    ) -> None:
        super().__init__(entries)
        self._owner = owner
        self._music_id = music_id

    def _bind(self, level: int, entry: ScoreEntry) -> ScoreEntry:
        entry._on_change = lambda: self._owner._track(
            self._music_id, level, entry, False
        )
        return entry

    def __getitem__(self, level: int) -> ScoreEntry | None:
        entry = super().__getitem__(level)
        return None if entry is None else self._bind(level, entry)

    def __setitem__(self, level: int, value: ScoreEntry | Mapping[str, Any]) -> None:
        level = int(level)
        if level not in DIFFICULTY_LEVELS:
            raise KeyError(f"非法难度：{level}")
        is_new = level not in self._entries
        super().__setitem__(level, value)
        entry = self._entries[level]
        self._bind(level, entry)
        self._owner._track(self._music_id, level, entry, is_new)
