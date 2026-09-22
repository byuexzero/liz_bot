"""单个 userId 的详细数据抓取。

移植自 sdgb ``getuserdata/updatedata.py``。

与旧版的差异
------------
旧版 ``from sdgb import sdgb_api`` 依赖 ``sdgb/sdgb.py``，
而那个模块把 SOCKS5 代理地址**硬编码在源码里**（具体值见存档 README）。
本实现改用 :class:`maimai.client.MaimaiClient`，代理从 ``.env`` 的
``PROXY_URL`` 读取，留空即直连。

数据库表结构**保持完全一致**，已有的 ``.db`` 文件可直接继续使用。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from ..client import MaimaiClient

__all__ = ["updatedata", "ensure_tables"]

logger = logging.getLogger(__name__)


def _date_to_minute_timestamp(date_str: str | None) -> Optional[int]:
    """把 ``"2023-06-09 23:54:18"`` 转成分钟级时间戳。"""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        return int(dt.timestamp()) // 60
    except Exception:
        return None


_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS user_character (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userId INTEGER,
        characterId INTEGER,
        point INTEGER,
        useCount INTEGER,
        level INTEGER,
        nextAwake INTEGER,
        nextAwakePercent INTEGER,
        awakening INTEGER,
        updateTime INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_item (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userId INTEGER,
        itemKind INTEGER,
        itemId INTEGER,
        stock INTEGER,
        isValid INTEGER,
        updateTime INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_course (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userId INTEGER,
        courseId INTEGER,
        isLastClear INTEGER,
        totalRestlife INTEGER,
        totalAchievement INTEGER,
        totalDeluxscore INTEGER,
        bestAchievement INTEGER,
        bestDeluxscore INTEGER,
        bestAchievementDate INTEGER,
        bestDeluxscoreDate INTEGER,
        playCount INTEGER,
        clearDate INTEGER,
        lastPlayDate INTEGER,
        extNum1 INTEGER,
        updateTime INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_map (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userId INTEGER,
        mapId INTEGER,
        distance INTEGER,
        isLock INTEGER,
        isClear INTEGER,
        isComplete INTEGER,
        unlockFlag INTEGER,
        updateTime INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_loginbonus (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userId INTEGER,
        bonusId INTEGER,
        point INTEGER,
        isCurrent INTEGER,
        isComplete INTEGER,
        updateTime INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_option (
        userId INTEGER PRIMARY KEY,
        optionKind INTEGER,
        noteSpeed INTEGER,
        slideSpeed INTEGER,
        touchSpeed INTEGER,
        noteSize INTEGER,
        slideSize INTEGER,
        touchSize INTEGER,
        tapDesign INTEGER,
        holdDesign INTEGER,
        slideDesign INTEGER,
        starType INTEGER,
        starRotate INTEGER,
        adjustTiming INTEGER,
        judgeTiming INTEGER,
        mirrorMode INTEGER,
        ansVolume INTEGER,
        tempoVolume INTEGER,
        tapHoldVolume INTEGER,
        touchHoldVolume INTEGER,
        breakVolume INTEGER,
        exVolume INTEGER,
        slideVolume INTEGER,
        breakSe INTEGER,
        slideSe INTEGER,
        exSe INTEGER,
        criticalSe INTEGER,
        tapSe INTEGER,
        headPhoneVolume INTEGER,
        matching INTEGER,
        brightness INTEGER,
        dispRate INTEGER,
        dispCenter INTEGER,
        dispJudge INTEGER,
        dispJudgePos INTEGER,
        dispJudgeTouchPos INTEGER,
        dispChain INTEGER,
        dispBar INTEGER,
        trackSkip INTEGER,
        touchEffect INTEGER,
        outlineDesign INTEGER,
        submonitorAnimation INTEGER,
        submonitorAppeal INTEGER,
        submonitorAchive INTEGER,
        sortTab INTEGER,
        sortMusic INTEGER,
        damageSeVolume INTEGER,
        touchVolume INTEGER,
        outFrameType INTEGER,
        breakSlideVolume INTEGER,
        updateTime INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_music (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userId INTEGER,
        musicId INTEGER,
        level INTEGER,
        playCount INTEGER,
        achievement INTEGER,
        comboStatus INTEGER,
        syncStatus INTEGER,
        deluxscoreMax INTEGER,
        scoreRank INTEGER,
        extNum1 INTEGER,
        extNum2 INTEGER,
        updateTime INTEGER
    )
    """,
)

#: ``GetUserItemApi`` 的分段起始下标。各段对应不同的道具种类。
_ITEM_RANGES: tuple[int, ...] = (
    100000000000,
    30000000000,
    40000000000,
    110000000000,
    120000000000,
    50000000000,
    60000000000,
    70000000000,
    80000000000,
)

#: ``user_option`` 表的列顺序，必须与建表语句一致
_OPTION_COLUMNS: tuple[str, ...] = (
    "userId", "optionKind", "noteSpeed", "slideSpeed", "touchSpeed",
    "noteSize", "slideSize", "touchSize", "tapDesign", "holdDesign",
    "slideDesign", "starType", "starRotate", "adjustTiming", "judgeTiming",
    "mirrorMode", "ansVolume", "tempoVolume", "tapHoldVolume", "touchHoldVolume",
    "breakVolume", "exVolume", "slideVolume", "breakSe", "slideSe",
    "exSe", "criticalSe", "tapSe", "headPhoneVolume", "matching",
    "brightness", "dispRate", "dispCenter", "dispJudge", "dispJudgePos",
    "dispJudgeTouchPos", "dispChain", "dispBar", "trackSkip", "touchEffect",
    "outlineDesign", "submonitorAnimation", "submonitorAppeal", "submonitorAchive",
    "sortTab", "sortMusic", "damageSeVolume", "touchVolume", "outFrameType",
    "breakSlideVolume", "updateTime",
)


def ensure_tables(conn: sqlite3.Connection) -> None:
    """按需建表。"""
    cur = conn.cursor()
    for statement in _SCHEMA:
        cur.execute(statement)
    conn.commit()


def _paginate(
    client: MaimaiClient,
    api_name: str,
    user_id: int,
    base_payload: Mapping[str, Any],
    list_key: str,
    page_size: int | None = None,
) -> list[dict[str, Any]]:
    """按 ``nextIndex`` 翻页拉取，直到返回空列表或 ``nextIndex == 0``。"""
    collected: list[dict[str, Any]] = []
    next_index = 0

    while True:
        payload = dict(base_payload)
        payload["userId"] = int(user_id)
        payload["nextIndex"] = int(next_index)
        if page_size is not None:
            payload["maxCount"] = page_size

        response = client.call_api_sync(api_name, payload, user_id)
        if not response:
            break

        entries = response.get(list_key)
        next_index = response.get("nextIndex", 0)
        if entries:
            collected.extend(entries)
        if not entries or next_index == 0:
            break

    return collected


def updatedata(
    user_id: int,
    timestamp: int,
    db_path: str = "./userdata.db",
    max_retries: int = 3,
) -> bool:
    """抓取指定用户的详细数据并写入 SQLite。

    :param user_id: 玩家 userId
    :param timestamp: 本次抓取的 Unix 时间戳，写入各表的 ``updateTime``
    :param db_path: SQLite 文件路径
    :param max_retries: 单次接口调用的重试次数（不含首次）
    :returns: 成功返回 ``True``
    :raises maimai.client.MaimaiApiError: 接口不可恢复地失败
    """
    client = MaimaiClient(retries=max_retries)
    conn = sqlite3.connect(db_path, timeout=30)

    try:
        ensure_tables(conn)
        cur = conn.cursor()

        # ------- 角色 -------
        response = client.call_api_sync(
            "GetUserCharacterApi", {"userId": int(user_id)}, user_id
        )
        cur.execute("DELETE FROM user_character WHERE userId = ?", (user_id,))
        cur.executemany(
            "INSERT INTO user_character (userId, characterId, point, useCount, "
            "level, nextAwake, nextAwakePercent, awakening, updateTime) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    user_id,
                    e.get("characterId"),
                    e.get("point"),
                    e.get("useCount"),
                    e.get("level"),
                    e.get("nextAwake"),
                    e.get("nextAwakePercent"),
                    e.get("awakening"),
                    timestamp,
                )
                for e in (response.get("userCharacterList") or [])
            ],
        )
        conn.commit()

        # ------- 道具（多个分段） -------
        all_items: list[dict[str, Any]] = []
        for start in _ITEM_RANGES:
            next_index = start
            while True:
                response = client.call_api_sync(
                    "GetUserItemApi",
                    {"userId": int(user_id), "nextIndex": int(next_index), "maxCount": 100},
                    user_id,
                )
                if not response:
                    break
                items = response.get("userItemList")
                next_index = response.get("nextIndex", 0)
                if items:
                    all_items.extend(items)
                if not items or next_index == 0:
                    break

        cur.execute("DELETE FROM user_item WHERE userId = ?", (user_id,))
        cur.executemany(
            "INSERT INTO user_item (userId, itemKind, itemId, stock, isValid, updateTime) "
            "VALUES (?,?,?,?,?,?)",
            [
                (
                    user_id,
                    it.get("itemKind"),
                    it.get("itemId"),
                    it.get("stock"),
                    int(bool(it.get("isValid"))),
                    timestamp,
                )
                for it in all_items
            ],
        )
        conn.commit()

        # ------- 段位 -------
        courses = _paginate(
            client, "GetUserCourseApi", user_id, {}, "userCourseList"
        )
        cur.execute("DELETE FROM user_course WHERE userId = ?", (user_id,))
        cur.executemany(
            "INSERT INTO user_course (userId, courseId, isLastClear, totalRestlife, "
            "totalAchievement, totalDeluxscore, bestAchievement, bestDeluxscore, "
            "bestAchievementDate, bestDeluxscoreDate, playCount, clearDate, "
            "lastPlayDate, extNum1, updateTime) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    user_id,
                    e.get("courseId"),
                    int(bool(e.get("isLastClear"))),
                    e.get("totalRestlife"),
                    e.get("totalAchievement"),
                    e.get("totalDeluxscore"),
                    e.get("bestAchievement"),
                    e.get("bestDeluxscore"),
                    _date_to_minute_timestamp(e.get("bestAchievementDate")),
                    _date_to_minute_timestamp(e.get("bestDeluxscoreDate")),
                    e.get("playCount"),
                    _date_to_minute_timestamp(e.get("clearDate")),
                    _date_to_minute_timestamp(e.get("lastPlayDate")),
                    e.get("extNum1"),
                    timestamp,
                )
                for e in courses
            ],
        )
        conn.commit()

        # ------- 地图 -------
        maps = _paginate(client, "GetUserMapApi", user_id, {}, "userMapList", 20)
        cur.execute("DELETE FROM user_map WHERE userId = ?", (user_id,))
        cur.executemany(
            "INSERT INTO user_map (userId, mapId, distance, isLock, isClear, "
            "isComplete, unlockFlag, updateTime) VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    user_id,
                    e.get("mapId"),
                    e.get("distance"),
                    int(bool(e.get("isLock"))),
                    int(bool(e.get("isClear"))),
                    int(bool(e.get("isComplete"))),
                    e.get("unlockFlag"),
                    timestamp,
                )
                for e in maps
            ],
        )
        conn.commit()

        # ------- 登录奖励 -------
        bonuses = _paginate(
            client, "GetUserLoginBonusApi", user_id, {}, "userLoginBonusList", 20
        )
        cur.execute("DELETE FROM user_loginbonus WHERE userId = ?", (user_id,))
        cur.executemany(
            "INSERT INTO user_loginbonus (userId, bonusId, point, isCurrent, "
            "isComplete, updateTime) VALUES (?,?,?,?,?,?)",
            [
                (
                    user_id,
                    e.get("bonusId"),
                    e.get("point"),
                    int(bool(e.get("isCurrent"))),
                    int(bool(e.get("isComplete"))),
                    timestamp,
                )
                for e in bonuses
            ],
        )
        conn.commit()

        # ------- 设置（单次调用） -------
        response = client.call_api_sync(
            "GetUserOptionApi", {"userId": int(user_id)}, user_id
        )
        if response:
            option = response.get("userOption") or {}
            values = []
            for column in _OPTION_COLUMNS:
                if column == "userId":
                    values.append(response.get("userId"))
                elif column == "updateTime":
                    values.append(timestamp)
                else:
                    values.append(option.get(column))
            placeholders = ",".join("?" for _ in _OPTION_COLUMNS)
            columns_sql = ",".join(_OPTION_COLUMNS)
            cur.execute(
                f"REPLACE INTO user_option ({columns_sql}) VALUES ({placeholders})",
                values,
            )
            conn.commit()

        # ------- 成绩 -------
        music_entries: list[dict[str, Any]] = []
        next_index = 0
        while True:
            response = client.call_api_sync(
                "GetUserMusicApi",
                {"userId": int(user_id), "nextIndex": int(next_index), "maxCount": 50},
                user_id,
            )
            if not response:
                break
            music_list = response.get("userMusicList")
            next_index = response.get("nextIndex", 0)
            if music_list:
                for detail in music_list:
                    music_entries.extend(detail.get("userMusicDetailList", []))
            if not music_list or next_index == 0:
                break

        cur.execute("DELETE FROM user_music WHERE userId = ?", (user_id,))
        cur.executemany(
            "INSERT INTO user_music (userId, musicId, level, playCount, achievement, "
            "comboStatus, syncStatus, deluxscoreMax, scoreRank, extNum1, extNum2, "
            "updateTime) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    user_id,
                    e.get("musicId"),
                    e.get("level"),
                    e.get("playCount"),
                    e.get("achievement"),
                    e.get("comboStatus"),
                    e.get("syncStatus"),
                    e.get("deluxscoreMax"),
                    e.get("scoreRank"),
                    e.get("extNum1"),
                    e.get("extNum2"),
                    timestamp,
                )
                for e in music_entries
            ],
        )
        conn.commit()

        return True
    finally:
        conn.close()
        client.close_sync()


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：抓取单个 userId。"""
    import argparse
    import time

    parser = argparse.ArgumentParser(description="抓取单个 userId 的详细数据到 SQLite")
    parser.add_argument("userId", type=int)
    parser.add_argument("--db", type=str, default="./userdata.db")
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args(argv)

    try:
        updatedata(args.userId, int(time.time()), args.db, args.retries)
        logger.info("updatedata 完成：userId=%s", args.userId)
        return 0
    except Exception as exc:
        logger.exception("updatedata 失败：userId=%s %s", args.userId, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
