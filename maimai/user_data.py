"""用户数据聚合层：容器驱动的增量上传。

移植自 Lionheart ``src/user.ts`` 的缓存与 ``save()``。

核心变化
--------
重构前，上传逻辑是**硬编码**的：``payloads.build_user_all`` 把请求体里
每一个字段手抄一遍，``userMusicDetailList`` 直接塞进调用方给的那一条成绩，
``userCharacterList`` / ``userItemList`` 永远是空数组。

现在改为**容器驱动**：

1. :meth:`UserData.load` 把 ``GetUser*Api`` 的响应装进容器层
   （:class:`~maimai.containers.ScoreSet` / ``CharacterSet`` / ``ItemSet`` / ``Mission``）。
2. 业务侧调用 :meth:`UserData.apply_play` 修改成绩，容器自动登记脏数据。
3. :meth:`UserData.build_upsert` 只导出**被改动过**的条目，
   并把 ``isNewXxxList`` 按 Lionheart 的约定拼成 ``"0"/"1"`` 串
   （0 = 覆盖已有槽位，1 = 追加新槽位）。

这样成绩、角色、道具、任务四类数据都走同一套增量逻辑，
不再需要为每类数据单独维护一份硬编码请求体。

与上游的差异
------------
Lionheart 的 ``generatePlaylog`` 会给没有显式 playlog 的成绩生成一条
``achievement = 0`` 的占位记录。本实现改为**沿用成绩本身的数值**，
与重构前 eaquira 的行为一致（playlog 的达成率就是本次游玩的达成率）。

关于判定明细
------------
判定数、连击数、同步数都**与具体乐曲的谱面绑定**，不存在能代表所有歌的
常量，所以 :data:`DEFAULT_NOTE_COUNTS` 一律为 0，只保留键结构。
真实数值由调用方通过 :attr:`PlayInput.note_counts` 传入；
``total_combo`` / ``max_sync`` 不传时按判定明细求和，
``max_combo`` / ``total_sync`` / ``ext_num4`` 不传时为 0。
这些字段在 :class:`PlayInput` 上都可显式覆盖。

（重构前这里抄的是 eaquira 针对某一首特定乐曲的硬编码值，
用在别的歌上等于伪造游玩数据，故改为全 0。）
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Iterator, Mapping, Sequence

import pytz

from . import config, payloads
from .client import MaimaiClient
from .containers import (
    CharacterSet,
    ItemSet,
    Mission,
    ScoreEntry,
    ScoreSet,
    unpack_present,
)
from .containers.score import convert_achievement_to_score_rank
from .typings.base import (
    MusicDifficultyID,
    PlayComboFlagID,
    PlaySyncFlagID,
    UserItemKind,
)

__all__ = [
    "UserData",
    "PlayInput",
    "DEFAULT_NOTE_COUNTS",
    "GAME_TZ",
    "PLAYLOGS_PER_CREDIT",
    "ITEM_KINDS",
    "calculate_deluxscore",
    "generate_playlog",
]

logger = logging.getLogger(__name__)

#: 游戏内时间使用的时区
GAME_TZ = pytz.timezone("Asia/Shanghai")

#: 一次投币最多包含的曲目数。上传时按此分组，一组一次 ``UpsertUserAllApi``。
PLAYLOGS_PER_CREDIT = 4

#: ``GetUserItemApi`` 的分段基数：``nextIndex = itemKind * 1e10``
ITEM_INDEX_BASE = 10_000_000_000

#: 需要拉取的道具种类。``Present`` 也要拉——礼物是复合 ID，
#: 需要解包后分发到 :data:`PRESENT_KINDS` 对应的容器。
#: Lionheart 里 ``musicSrg`` 被注释掉了，此处同样不含。
ITEM_KINDS: tuple[int, ...] = (
    UserItemKind.Plate,
    UserItemKind.Title,
    UserItemKind.Icon,
    UserItemKind.Present,
    UserItemKind.Frame,
    UserItemKind.Ticket,
    UserItemKind.Music,
    UserItemKind.MusicMas,
    UserItemKind.MusicRem,
    UserItemKind.Character,
    UserItemKind.Partner,
)

#: 礼物容器覆盖的种类（``ItemSet(present=True)``）
PRESENT_KINDS: tuple[int, ...] = (
    UserItemKind.Plate,
    UserItemKind.Title,
    UserItemKind.Icon,
    UserItemKind.Music,
    UserItemKind.Character,
    UserItemKind.Partner,
    UserItemKind.Frame,
    UserItemKind.Ticket,
)

#: ``upsertUserAll.userData`` 不接受、必须剔除的字段。
#: 游戏端会自行推导这些值，回传反而会写坏数据。
_USER_DATA_DROP: tuple[str, ...] = (
    "friendCode",
    "nameplateId",
    "cmLastEmoneyBrand",
    "trophyId",
    "cmLastEmoneyCredit",
)

#: ``userOption`` 必须剔除的字段
_OPTION_DROP: tuple[str, ...] = ("tempoVolume",)

#: ``userRating.udemae`` 必须剔除的字段（段位内部计数，非持久化字段）
_RATING_UDEMAE_DROP: tuple[str, ...] = (
    "MaxLoseNum",
    "NpcLoseNum",
    "NpcMaxLoseNum",
    "NpcMaxWinNum",
    "NpcTotalLoseNum",
    "NpcTotalWinNum",
    "NpcWinNum",
)

#: 判定区前缀
_NOTE_ZONES: tuple[str, ...] = ("tap", "hold", "slide", "touch", "break")

#: 判定档位后缀
_NOTE_RANKS: tuple[str, ...] = (
    "CriticalPerfect",
    "Perfect",
    "Great",
    "Good",
    "Miss",
)

#: 判定明细的**键集合**，数值一律为 0。
#:
#: 判定数与具体乐曲的谱面绑定（物量、各类 note 的分布每首歌都不同），
#: 不存在一组能代表所有歌的常量，因此这里只保留**结构**，
#: 真实数值必须由调用方通过 :attr:`PlayInput.note_counts` 传入。
#:
#: 历史背景：重构前这里抄的是 eaquira 的硬编码值
#: （``tapCriticalPerfect: 101``、``holdCriticalPerfect: 9`` 等），
#: 那套数字只对应某一首特定乐曲；而且当时 ``totalCombo`` 被写成 128，
#: 与判定数之和 115 本身就不自洽。用在别的歌上等于伪造游玩数据。
DEFAULT_NOTE_COUNTS: dict[str, int] = {
    **{f"{zone}{rank}": 0 for zone in _NOTE_ZONES for rank in _NOTE_RANKS},
    "fastCount": 0,
    "lateCount": 0,
}


def _now() -> datetime:
    return datetime.now(GAME_TZ)


def _fmt_datetime(dt: datetime) -> str:
    """``"2026-09-22 18:44:09.0"``，对应 Lionheart 的 ``toLocalDateTimeString(dt, true)``。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S") + ".0"


def _drop(mapping: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """浅拷贝并剔除指定键。"""
    dropped = set(keys)
    return {k: v for k, v in mapping.items() if k not in dropped}


def _is_new_flags(items: Sequence[Mapping[str, Any]]) -> str:
    """把容器导出的 ``isNew`` 列表拼成 ``"0101"`` 形式的字符串。

    Lionheart 用它标记槽位是"覆盖已有"（``0``）还是"追加新槽位"（``1``）。
    """
    return "".join("1" if item.get("isNew") else "0" for item in items)


def calculate_deluxscore(counts: Mapping[str, int]) -> int:
    """按判定明细计算 DX 分数。

    等价于 Lionheart 的 ``calculateDeluxscore``：
    CriticalPerfect 计 3 分、Perfect 计 2 分、Great 计 1 分，其余不计。
    """
    total = 0
    for zone in _NOTE_ZONES:
        total += int(counts.get(f"{zone}CriticalPerfect") or 0) * 3
        total += int(counts.get(f"{zone}Perfect") or 0) * 2
        total += int(counts.get(f"{zone}Great") or 0) * 1
    return total


def _note_total(counts: Mapping[str, int]) -> int:
    """判定明细的 note 总数。

    等价于 Lionheart 里把 25 个判定字段相加的那一长串表达式。
    注意**不含** ``fastCount`` / ``lateCount``——那两个是 FAST/LATE 计数，
    不是 note 数。
    """
    return sum(
        int(counts.get(f"{zone}{rank}") or 0)
        for zone in _NOTE_ZONES
        for rank in _NOTE_RANKS
    )


@dataclass
class PlayInput:
    """一次游玩的输入。

    :param music_id: 乐曲 ID
    :param level: 难度（:class:`~maimai.typings.base.MusicDifficultyID`）
    :param achievement: 达成率，如 ``1005000`` 表示 100.5%
    :param combo_status: 连击状态（:class:`PlayComboFlagID`）
    :param sync_status: 同步状态（:class:`PlaySyncFlagID`）
    :param deluxscore_max: DX 分数
    :param ext_num1: 扩展字段，Utage 谱面用
    :param note_counts: 判定明细。判定数与具体乐曲绑定，
        **不传就是全 0**（见 :data:`DEFAULT_NOTE_COUNTS`），
        想要真实数值必须由调用方按谱面填。
    :param total_combo: 总连击。``None`` 表示按 ``note_counts`` 求和
    :param max_combo: 最大连击。``None`` 表示 0
    :param max_sync: 最大同步。``None`` 表示按 ``note_counts`` 求和
    :param total_sync: 总同步。``None`` 表示 0
    :param ext_num4: 扩展字段。``None`` 表示 0
    :param is_clear: 是否通关。``None`` 表示按达成率判断（>= 500000）
    :param track_no: 本次投币中的第几首（1..4）
    """

    music_id: int
    level: int
    achievement: int
    combo_status: int = int(PlayComboFlagID.None_)
    sync_status: int = int(PlaySyncFlagID.None_)
    deluxscore_max: int = 0
    ext_num1: int = 0
    note_counts: Mapping[str, int] | None = None
    total_combo: int | None = None
    max_combo: int | None = None
    max_sync: int | None = None
    total_sync: int | None = None
    ext_num4: int | None = None
    is_clear: bool | None = None
    track_no: int = 1

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PlayInput":
        """从 ``music_data`` 形式的字典构造。

        兼容重构前的字段名（``musicId`` / ``level`` / ``achievement`` /
        ``comboStatus`` / ``syncStatus`` / ``deluxscoreMax`` / ``scoreRank``）。
        ``scoreRank`` 会被忽略——它由达成率推导，容器里是只读属性。

        判定明细可以用 ``noteCounts`` 传入，也可以直接把 25 个判定字段
        平铺在字典里（``tapCriticalPerfect`` 等），后者会被自动收集。
        """

        def pick(key: str, default: Any) -> Any:
            value = data.get(key)
            return default if value is None else value

        note_counts = data.get("noteCounts")
        if note_counts is None:
            inline = {
                key: int(data[key])
                for key in DEFAULT_NOTE_COUNTS
                if key in data and data[key] is not None
            }
            note_counts = inline or None

        return cls(
            music_id=int(pick("musicId", 0)),
            level=int(pick("level", MusicDifficultyID.Basic)),
            achievement=int(pick("achievement", 0)),
            combo_status=int(pick("comboStatus", PlayComboFlagID.None_)),
            sync_status=int(pick("syncStatus", PlaySyncFlagID.None_)),
            deluxscore_max=int(pick("deluxscoreMax", 0)),
            ext_num1=int(pick("extNum1", 0)),
            note_counts=note_counts,
            total_combo=data.get("totalCombo"),
            max_combo=data.get("maxCombo"),
            max_sync=data.get("maxSync"),
            total_sync=data.get("totalSync"),
            ext_num4=data.get("extNum4"),
            is_clear=data.get("isClear"),
            track_no=int(pick("trackNo", 1)),
        )


def generate_playlog(
    entry: ScoreEntry,
    *,
    music_id: int,
    login_id: int,
    login_date: int,
    version: int,
    place_id: int,
    place_name: str,
    chara_slots: Sequence[int],
    note_counts: Mapping[str, int] | None = None,
    total_combo: int | None = None,
    max_combo: int | None = None,
    max_sync: int | None = None,
    total_sync: int | None = None,
    ext_num4: int | None = None,
    is_clear: bool | None = None,
    track_no: int = 1,
) -> dict[str, Any]:
    """由一条成绩生成 ``UserPlaylog``。

    ``achievement`` / ``scoreRank`` / ``comboStatus`` / ``syncStatus`` /
    ``deluxscore`` 都取自 ``entry``，因此调用方只需保证
    :meth:`UserData.apply_play` 已经把本次游玩写进容器。

    判定明细与连击/同步数都与谱面绑定，**不传就是 0**；
    其中 ``total_combo`` / ``max_sync`` 不传时按判定明细求和
    （判定明细也是 0 的话结果就是 0）。
    """
    # 始终以完整的键结构为基础：调用方只给部分判定项时，
    # 缺的补 0，避免上传一份缺字段的 playlog。
    counts = dict(DEFAULT_NOTE_COUNTS)
    if note_counts:
        unknown = sorted(set(note_counts) - set(counts))
        if unknown:
            logger.warning("判定明细含未知字段，已忽略：%s", unknown)
        for key, value in note_counts.items():
            if key in counts and value is not None:
                counts[key] = int(value)

    achievement = int(entry.achievement)
    utage = (
        achievement > 1010000 and int(entry.level) == int(MusicDifficultyID.Utage)
    )

    # 连击/同步数同样与谱面绑定：调用方没给就按判定明细求和
    # （判定明细默认全 0，所以默认结果也是 0），与 Lionheart 一致。
    derived = _note_total(counts)

    playlog: dict[str, Any] = {
        "userId": 0,
        "orderId": 0,
        "playlogId": login_id,
        "version": version,
        "placeId": place_id,
        "placeName": place_name,
        "loginDate": login_date,
        "playDate": "",
        "userPlayDate": "",
        "type": 0,
        "musicId": music_id,
        "level": int(entry.level),
        "trackNo": track_no,
        "vsMode": 0,
        "vsUserName": "",
        "vsStatus": 0,
        "vsUserRating": 0,
        "vsUserAchievement": 0,
        "vsUserGradeRank": 0,
        "vsRank": 0,
        "playerNum": 1,
        "playedUserId1": 0,
        "playedUserName1": "",
        "playedMusicLevel1": 0,
        "playedUserId2": 0,
        "playedUserName2": "",
        "playedMusicLevel2": 0,
        "playedUserId3": 0,
        "playedUserName3": "",
        "playedMusicLevel3": 0,
    }

    # 五个角色槽位
    for slot in range(1, 6):
        playlog[f"characterId{slot}"] = (
            int(chara_slots[slot - 1]) if slot - 1 < len(chara_slots) else 0
        )
        playlog[f"characterLevel{slot}"] = 1
        playlog[f"characterAwakening{slot}"] = 0

    playlog.update(
        {
            "achievement": achievement,
            "deluxscore": int(entry.deluxscore_max),
            "scoreRank": convert_achievement_to_score_rank(achievement, utage),
            "maxCombo": 0 if max_combo is None else int(max_combo),
            "totalCombo": derived if total_combo is None else int(total_combo),
            "maxSync": derived if max_sync is None else int(max_sync),
            "totalSync": 0 if total_sync is None else int(total_sync),
            "isTap": True,
            "isHold": True,
            "isSlide": True,
            "isTouch": False,
            "isBreak": True,
            "isCriticalDisp": True,
            "isFastLateDisp": True,
            "isAchieveNewRecord": False,
            "isDeluxscoreNewRecord": False,
            "comboStatus": int(entry.combo_status),
            "syncStatus": int(entry.sync_status),
            "isClear": (achievement >= 500000) if is_clear is None else bool(is_clear),
            "beforeGrade": 0,
            "afterGrade": 0,
            "afterGradeRank": 0,
            "isPlayTutorial": False,
            "isEventMode": False,
            "isFreedomMode": False,
            "playMode": 0,
            "isNewFree": False,
            "trialPlayAchievement": -1,
            "extNum1": 0,
            "extNum2": 0,
            "extNum4": 0 if ext_num4 is None else int(ext_num4),
            # Buddy 谱面（Utage 且达成率 > 101%）需要置位，否则服务端会拒绝
            "extBool1": utage,
            "extBool2": False,
        }
    )
    playlog.update(counts)
    return playlog


@dataclass
class UserData:
    """某个 userId 的完整游戏数据。

    容器字段（``score`` / ``character`` / ``items`` / ``presents`` / ``mission``）
    带脏跟踪；其余 ``data`` / ``option`` / ``extend`` / ``rating`` /
    ``charge`` / ``activity`` 是原样保留的字典，上传时整块回传。
    """

    user_id: int
    client: MaimaiClient

    data: dict[str, Any] = field(default_factory=dict)
    option: dict[str, Any] = field(default_factory=dict)
    extend: dict[str, Any] = field(default_factory=dict)
    rating: dict[str, Any] = field(default_factory=dict)
    charge: list[dict[str, Any]] = field(default_factory=list)
    activity: dict[str, Any] = field(default_factory=dict)

    score: ScoreSet = field(default_factory=ScoreSet)
    character: CharacterSet = field(default_factory=CharacterSet)
    mission: Mission = field(default_factory=Mission)

    #: ``itemKind -> ItemSet``（普通道具）
    items: dict[int, ItemSet] = field(default_factory=dict)
    #: ``itemKind -> ItemSet``（礼物，导出时会把 itemId 打包回复合值）
    presents: dict[int, ItemSet] = field(default_factory=dict)

    #: 加载时间，供 playlog 的时间戳使用
    loaded_at: datetime | None = None

    # ---- 加载 -----------------------------------------------------------
    @classmethod
    async def load(
        cls,
        client: MaimaiClient,
        user_id: int,
        *,
        include_items: bool = True,
    ) -> "UserData":
        """拉取并装载全部容器。

        :param include_items: 是否拉取道具/礼物。道具按种类分段翻页，
            请求数较多，不需要时可关掉。
        """
        instance = cls(user_id=user_id, client=client)
        await instance.reload(include_items=include_items)
        return instance

    async def reload(self, *, include_items: bool = True) -> None:
        """（重新）拉取数据并重置容器。"""
        uid = self.user_id
        tasks: list[Any] = [
            self.client.request("GetUserDataApi", {"userId": uid}, uid),
            self.client.request("GetUserOptionApi", {"userId": uid}, uid),
            self.client.request("GetUserExtendApi", {"userId": uid}, uid),
            self.client.request("GetUserRatingApi", {"userId": uid}, uid),
            self.client.request("GetUserChargeApi", {"userId": uid}, uid),
            self.client.request("GetUserActivityApi", {"userId": uid}, uid),
            self.client.request("GetUserMissionDataApi", {"userId": uid}, uid),
            self.client.request("GetUserCharacterApi", {"userId": uid}, uid),
            self._fetch_music(),
        ]
        if include_items:
            tasks.append(self._fetch_items())

        (
            data_resp,
            option_resp,
            extend_resp,
            rating_resp,
            charge_resp,
            activity_resp,
            mission_resp,
            character_resp,
            music_details,
            *item_rest,
        ) = await asyncio.gather(*tasks)

        # ---- 简单结构 ----
        self.data = _drop(
            {**(data_resp.get("userData") or {}), "banState": data_resp.get("banState", 0)},
            _USER_DATA_DROP,
        )
        self.option = _drop(option_resp.get("userOption") or {}, _OPTION_DROP)
        self.extend = dict(extend_resp.get("userExtend") or {})
        self.rating = _merge_rating(rating_resp.get("userRating") or {})
        self.charge = list(charge_resp.get("userChargeList") or [])
        self.activity = dict(activity_resp.get("userActivity") or {})

        # ---- 容器 ----
        self.score = ScoreSet(music_details)
        self.character = CharacterSet(character_resp.get("userCharacterList") or [])
        self.mission = Mission(
            mission_resp.get("userWeeklyData") or {},
            mission_resp.get("userMissionDataList") or [],
        )

        if include_items:
            self._load_items(item_rest[0])

        self.loaded_at = _now()
        logger.info(
            "userId=%s 装载完成：%d 首成绩、%d 个角色、%d 条任务",
            uid,
            len(self.score),
            len(self.character),
            len(self.mission.mission),
        )

    async def _fetch_music(self) -> list[dict[str, Any]]:
        """翻页拉取全部成绩明细（``GetUserMusicApi``）。"""
        details: list[dict[str, Any]] = []
        next_index = 0
        while True:
            response = await self.client.request(
                "GetUserMusicApi",
                {"userId": self.user_id, "nextIndex": next_index, "maxCount": 10000},
                self.user_id,
            )
            music_list = response.get("userMusicList") or []
            next_index = int(response.get("nextIndex") or 0)
            for item in music_list:
                details.extend(item.get("userMusicDetailList") or [])
            if not music_list or next_index == 0:
                return details

    async def _fetch_items(self) -> dict[int, list[dict[str, Any]]]:
        """按种类分段翻页拉取道具（``GetUserItemApi``）。

        ``nextIndex`` 的起始值为 ``itemKind * 1e10``。
        注意 ``maimai/crawler/updatedata.py`` 里的 ``_ITEM_RANGES`` 是手工枚举的，
        漏掉了 Plate(1) / Title(2) / Character(9) 三种；这里改用通式，覆盖完整。
        """
        result: dict[int, list[dict[str, Any]]] = {}
        for kind in ITEM_KINDS:
            collected: list[dict[str, Any]] = []
            next_index = int(kind) * ITEM_INDEX_BASE
            while True:
                response = await self.client.request(
                    "GetUserItemApi",
                    {
                        "userId": self.user_id,
                        "nextIndex": next_index,
                        "maxCount": 100,
                    },
                    self.user_id,
                )
                items = response.get("userItemList") or []
                next_index = int(response.get("nextIndex") or 0)
                collected.extend(items)
                if not items or next_index == 0:
                    break
            result[int(kind)] = collected
        return result

    def _load_items(self, by_kind: Mapping[int, list[dict[str, Any]]]) -> None:
        """把道具分成普通道具与礼物两组容器。"""
        self.items = {}
        self.presents = {kind: ItemSet((), kind, True) for kind in PRESENT_KINDS}
        present_kind = int(UserItemKind.Present)

        for kind, entries in by_kind.items():
            if int(kind) == present_kind:
                self._load_presents(entries)
                continue
            self.items[int(kind)] = ItemSet(entries, kind, False)

    def _load_presents(self, entries: Iterable[Mapping[str, Any]]) -> None:
        """解包礼物并按真实种类分发。

        礼物 ID 是 ``itemKind * 1e6 + itemId`` 的复合值。解包后若落在
        ``Present`` / ``MusicMas`` / ``MusicRem`` / ``MusicSrg``
        这些不收礼物的种类上，按 Lionheart 的注释直接忽略。
        """
        for entry in entries:
            if not entry.get("isValid", True):
                continue
            inner_kind, inner_id = unpack_present(int(entry.get("itemId") or 0))
            bucket = self.presents.get(inner_kind)
            if bucket is None:
                logger.debug("忽略无法归类的礼物：itemId=%s", entry.get("itemId"))
                continue
            # 装载阶段直接写底层字典：这是"读取"而不是"修改"，
            # 不应产生脏数据。
            bucket._item[inner_id] = int(entry.get("stock") or 0)

    # ---- 业务写入 -------------------------------------------------------
    def apply_play(self, play: PlayInput | Mapping[str, Any]) -> ScoreEntry:
        """把一次游玩写进成绩容器，并登记为待上传变更。

        已有成绩会 ``playCount + 1``；没有的会新建（导出时标 ``isNew=True``）。
        """
        if isinstance(play, Mapping):
            play = PlayInput.from_mapping(play)

        score = self.score.get(play.music_id)
        entry = score[play.level]
        if entry is None:
            # 新难度：先建壳再改，__setitem__ 会登记 isNew=True
            score[play.level] = ScoreEntry(level=play.level)
            entry = score[play.level]
            assert entry is not None  # ScoreEntry.__setitem__ 必定写入

        entry.play_count += 1
        entry.achievement = play.achievement
        entry.combo_status = play.combo_status
        entry.sync_status = play.sync_status
        entry.deluxscore_max = max(entry.deluxscore_max, play.deluxscore_max)
        entry.ext_num1 = play.ext_num1

        logger.info(
            "userId=%s 登记成绩：musicId=%s level=%s achievement=%s（playCount=%s）",
            self.user_id,
            play.music_id,
            play.level,
            play.achievement,
            entry.play_count,
        )
        return entry

    # ---- 导出 -----------------------------------------------------------
    def _prepare(
        self,
        *,
        login_id: int,
        login_date_time: int,
        plays: Sequence[PlayInput],
        version: int,
        place_id: int,
        place_name: str,
    ) -> list[tuple[dict[str, Any], bool, PlayInput | None]]:
        """把成绩增量与调用方给的游玩明细按 ``musicId + level`` 对齐。

        :returns: ``[(musicDetail, isNew, playOrNone), ...]``
        :raises ValueError: 容器里没有任何成绩可上传
        """
        music_detail = self.score.export()
        if not music_detail:
            raise ValueError(
                "成绩容器里没有待上传的变更；请先调用 apply_play()，"
                "或确认 load() 是否真的拉到了数据"
            )

        by_key = {(p.music_id, p.level): p for p in plays}
        aligned: list[tuple[dict[str, Any], bool, PlayInput | None]] = []
        for item in music_detail:
            detail = item["value"]
            key = (int(detail["musicId"]), int(detail["level"]))
            aligned.append((detail, bool(item["isNew"]), by_key.get(key)))
        return aligned

    def iter_upsert_batches(
        self,
        *,
        login_id: int,
        login_date: str,
        login_date_time: int,
        plays: Sequence[PlayInput] = (),
        client_id: str | None = None,
        region_id: int | None = None,
        region_name: str | None = None,
        place_id: int | None = None,
        place_name: str | None = None,
        event_mode: bool = False,
        free_play: bool = False,
        now: datetime | None = None,
        version: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """按**投币**分组产出 ``UpsertUserAllApi`` 请求体。

        一次投币最多 :data:`PLAYLOGS_PER_CREDIT` 首（默认 4），
        所以待上传成绩超过 4 条时必须拆成多次请求——这正是 Lionheart
        在 ``save()`` 里对 ``playlogList`` 做 ``slice(i, i + 4)`` 的原因。

        调用方在两次请求之间需要**重新登录**（旧登录已失效），
        并且 ``login_id`` / ``login_date`` 要用新的值。

        :raises ValueError: 容器里没有任何成绩可上传
        """
        moment = now or _now()
        cid = client_id or config.CLIENT_ID
        rid = config.REGION_ID if region_id is None else region_id
        rname = config.REGION_NAME if region_name is None else region_name
        pid = config.PLACE_ID if place_id is None else place_id
        pname = config.PLACE_NAME if place_name is None else place_name
        ver = (
            payloads.version_number(config.MAIMAI_ENCODING)
            if version is None
            else version
        )

        aligned = self._prepare(
            login_id=login_id,
            login_date_time=login_date_time,
            plays=plays,
            version=ver,
            place_id=pid,
            place_name=pname,
        )
        chara_slots = list(self.data.get("charaSlot") or [])

        for start in range(0, len(aligned), PLAYLOGS_PER_CREDIT):
            chunk = aligned[start : start + PLAYLOGS_PER_CREDIT]
            playlog_list: list[dict[str, Any]] = []
            for offset, (detail, _is_new, play) in enumerate(chunk):
                key = (int(detail["musicId"]), int(detail["level"]))
                entry = self.score.get(key[0])[key[1]]
                if entry is None:  # 理论上不可能：导出即来自容器
                    continue
                playlog_list.append(
                    generate_playlog(
                        entry,
                        music_id=key[0],
                        login_id=login_id,
                        login_date=login_date_time,
                        version=ver,
                        place_id=pid,
                        place_name=pname,
                        chara_slots=chara_slots,
                        note_counts=None if play is None else play.note_counts,
                        total_combo=None if play is None else play.total_combo,
                        max_combo=None if play is None else play.max_combo,
                        max_sync=None if play is None else play.max_sync,
                        total_sync=None if play is None else play.total_sync,
                        ext_num4=None if play is None else play.ext_num4,
                        is_clear=None if play is None else play.is_clear,
                        track_no=offset + 1,
                    )
                )

            yield self._build_payload(
                chunk=chunk,
                playlog_list=playlog_list,
                login_id=login_id,
                login_date=login_date,
                login_date_time=login_date_time,
                moment=moment,
                cid=cid,
                rid=rid,
                rname=rname,
                pid=pid,
                pname=pname,
                event_mode=event_mode,
                free_play=free_play,
                is_first=(start == 0),
            )

    def build_upsert(
        self,
        *,
        login_id: int,
        login_date: str,
        login_date_time: int,
        plays: Sequence[PlayInput] = (),
        client_id: str | None = None,
        region_id: int | None = None,
        region_name: str | None = None,
        place_id: int | None = None,
        place_name: str | None = None,
        event_mode: bool = False,
        free_play: bool = False,
        now: datetime | None = None,
        version: int | None = None,
    ) -> dict[str, Any]:
        """构造**单次** ``UpsertUserAllApi`` 请求体。

        ``userMusicDetailList`` 只含容器里被改动过的成绩；
        角色、道具、任务同样走增量导出，``isNewXxxList`` 按
        Lionheart 的约定拼成 ``"0"/"1"`` 串。

        待上传成绩超过 :data:`PLAYLOGS_PER_CREDIT` 条时请改用
        :meth:`iter_upsert_batches`，否则会抛 ``ValueError``。

        :param login_id: ``UserLoginApi`` 返回的 ``loginId``
        :param login_date: ``UserLoginApi`` 返回的 ``lastLoginDate``
        :param login_date_time: ``UserLoginApi`` 返回的 ``loginDateTime``（Unix 秒）
        :param plays: 与本次上传配套的游玩明细，按 ``musicId + level``
            与容器导出的成绩对齐
        :raises ValueError: 容器里没有待上传成绩，或超过一次投币的容量
        """
        batches = list(
            self.iter_upsert_batches(
                login_id=login_id,
                login_date=login_date,
                login_date_time=login_date_time,
                plays=plays,
                client_id=client_id,
                region_id=region_id,
                region_name=region_name,
                place_id=place_id,
                place_name=place_name,
                event_mode=event_mode,
                free_play=free_play,
                now=now,
                version=version,
            )
        )
        if len(batches) != 1:
            raise ValueError(
                f"待上传成绩有 {self.score.modified_count} 条，"
                f"需要 {len(batches)} 次请求（每次最多 {PLAYLOGS_PER_CREDIT} 首）；"
                "请改用 iter_upsert_batches() 并在批次之间重新登录"
            )
        return batches[0]

    def _build_payload(
        self,
        *,
        chunk: Sequence[tuple[dict[str, Any], bool, PlayInput | None]],
        playlog_list: Sequence[dict[str, Any]],
        login_id: int,
        login_date: str,
        login_date_time: int,
        moment: datetime,
        cid: str,
        rid: int,
        rname: str,
        pid: int,
        pname: str,
        event_mode: bool,
        free_play: bool,
        is_first: bool,
    ) -> dict[str, Any]:
        """把一批成绩 + playlog 组装成完整的 ``UpsertUserAllApi`` 请求体。

        :param is_first: 是否为本轮的第一个批次。角色与道具只在第一批上传
            （与 Lionheart 一致）——它们是"整块状态"而非"每投币一次的流水"，
            后续批次重复提交没有意义，还会放大请求体。
        """
        timestamp = int(moment.timestamp())
        now_str = _fmt_datetime(moment)

        if is_first:
            characters = self.character.export()
            item_list = self._export_items()
            character_values = [c["value"] for c in characters]
            character_flags = _is_new_flags(characters)
            item_values = [i["value"] for i in item_list]
            item_flags = _is_new_flags(item_list)
        else:
            character_values, character_flags = [], ""
            item_values, item_flags = [], ""

        music_slice = [detail for detail, _is_new, _play in chunk]
        new_slice = [{"isNew": is_new} for _d, is_new, _p in chunk]

        upsert_user_data: dict[str, Any] = {
            "userData": [
                {
                    **self.data,
                    "accessCode": "",
                    "lastGameId": config.CODENAME,
                    "lastPlaceId": pid,
                    "lastPlaceName": pname,
                    "lastRegionId": rid,
                    "lastRegionName": rname,
                    "lastClientId": cid,
                    "lastLoginDate": login_date,
                    "lastPlayDate": now_str,
                    "dateTime": timestamp,
                }
            ],
            "userExtend": [self.extend],
            "userOption": [self.option],
            "userCharacterList": character_values,
            "isNewCharacterList": character_flags,
            "userItemList": item_values,
            "isNewItemList": item_flags,
            "userMusicDetailList": music_slice,
            "isNewMusicDetailList": _is_new_flags(new_slice),
            "userRatingList": [self.rating],
            "userChargeList": [
                {
                    "chargeId": c.get("chargeId"),
                    "stock": c.get("stock"),
                    "purchaseDate": c.get("purchaseDate"),
                    "validDate": c.get("validDate"),
                }
                for c in self.charge
            ],
            "userActivityList": [self.activity],
            "userWeeklyData": self.mission.weekly,
            "userMissionDataList": self.mission.mission,
            "userGamePlaylogList": [
                {
                    "playlogId": login_id,
                    "version": self.data.get("lastRomVersion", ""),
                    "playDate": now_str,
                    "playMode": 0,
                    "useTicketId": -1,
                    "playCredit": 0 if free_play else 1,
                    "playTrack": len(playlog_list),
                    "clientId": cid,
                    "isPlayTutorial": False,
                    "isEventMode": event_mode,
                    "isNewFree": False,
                    "playCount": 0,
                    # 反作弊用的伪随机数
                    "playSpecial": payloads.calc_random(),
                    "playOtherUserId": 0,
                }
            ],
            "user2pPlaylog": {
                "userId1": 0,
                "userId2": 0,
                "userName1": "",
                "userName2": "",
                "regionId": 0,
                "placeId": 0,
                "user2pPlaylogDetailList": [],
            },
            # ---- 上游标了 TODO / 一律留空的字段 ----
            "userGhost": [],
            "userMapList": [],
            "userLoginBonusList": [],
            "userCourseList": [],
            "userFavoriteList": [],
            "userFriendSeasonRankingList": [],
            "userIntimateList": [],
            "isNewUserIntimateList": "",
            "userKaleidxScopeList": [],
            "isNewKaleidxScopeList": "",
            "userShopItemStockList": [],
            "userGetPointList": [],
            "userTradeItemList": [],
            "userFavoritemusicList": [],
            "isNewFavoritemusicList": "",
            "isNewMapList": "",
            "isNewLoginBonusList": "",
            "isNewCourseList": "",
            "isNewFavoriteList": "",
            "isNewFriendSeasonRankingList": "",
        }

        return {
            "userId": self.user_id,
            "playlogId": login_id,
            "isEventMode": event_mode,
            "isFreePlay": free_play,
            "loginDateTime": login_date_time,
            "userPlaylogList": list(playlog_list),
            "upsertUserAll": upsert_user_data,
        }

    def _export_items(self) -> list[dict[str, Any]]:
        """合并普通道具与礼物的增量，顺序与 Lionheart 一致。"""
        order = (
            UserItemKind.Plate,
            UserItemKind.Title,
            UserItemKind.Partner,
            UserItemKind.Icon,
        )
        merged: list[dict[str, Any]] = []
        for kind in order:
            bucket = self.items.get(kind)
            if bucket is not None:
                merged.extend(bucket.export())
        for kind in PRESENT_KINDS:
            bucket = self.presents.get(kind)
            if bucket is not None:
                merged.extend(bucket.export())
        for kind in (
            UserItemKind.Frame,
            UserItemKind.Ticket,
            UserItemKind.Music,
            UserItemKind.MusicMas,
            UserItemKind.MusicRem,
        ):
            bucket = self.items.get(kind)
            if bucket is not None:
                merged.extend(bucket.export())
        return merged

    def reset(self) -> None:
        """丢弃全部待上传变更（已加载的数据本身保留）。"""
        self.score.reset()
        self.character._modified.clear()
        for bucket in (*self.items.values(), *self.presents.values()):
            bucket._modified.clear()

    @property
    def modified_count(self) -> int:
        """待上传的变更总条数。"""
        return (
            self.score.modified_count
            + self.character.modified_count
            + sum(b.modified_count for b in self.items.values())
            + sum(b.modified_count for b in self.presents.values())
        )


def _merge_rating(rating: Mapping[str, Any]) -> dict[str, Any]:
    """剔除 ``udemae`` 里的内部计数字段（与 Lionheart 一致）。"""
    merged = dict(rating)
    udemae = merged.get("udemae")
    if isinstance(udemae, Mapping):
        merged["udemae"] = _drop(udemae, _RATING_UDEMAE_DROP)
    return merged
