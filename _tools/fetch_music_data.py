"""从水鱼（diving-fish）拉取完整乐曲信息到本地。

数据源
------
``GET https://www.diving-fish.com/api/maimaidxprober/music_data``
免鉴权、无需 Token，一次返回现行版本下全部乐曲的完整信息。

为什么做成离线脚本，而不是运行时拉取
------------------------------------
查歌是高频只读操作，而曲库只在游戏更新版本时变化（一个月一两次）。
若改成启动时拉取，每次冷启动都要依赖外网：网络抖动就等于机器人起不来，
而且容器里拉一次 900KB 也要占用启动时间。所以曲库**随镜像分发**，
本脚本只在需要更新曲库时手动跑一次。

ETag 缓存
---------
把上一次响应的 ETag 写到同目录的 ``.etag``，下次带上 ``If-None-Match``。
曲库没变时服务端回 304，省掉约 900KB 流量 —— 定时任务里可以放心反复跑。

先校验再落盘
------------
响应可能是网关错误页、限流提示、或字段被上游改动。若直接覆盖，
一次失败就会把好数据毁掉，而且查歌要等到有人用才发现。
因此这里先完整解析 + 结构校验，全部通过才原子替换。

用法::

    python _tools/fetch_music_data.py              # 增量（带 ETag）
    python _tools/fetch_music_data.py --force      # 强制重新下载
    python _tools/fetch_music_data.py --out X.json # 换输出路径
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://www.diving-fish.com/api/maimaidxprober/music_data"

# 相对本文件定位，避免依赖 cwd
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = _REPO_ROOT / "liz_bot" / "divingfish_songs" / "music_data.json"

# 每首乐曲必须具备的顶层字段。少一个就说明上游结构变了，宁可不更新。
REQUIRED_SONG_KEYS = {"id", "title", "type", "ds", "level", "cids", "charts", "basic_info"}
REQUIRED_BASIC_KEYS = {"title", "artist", "genre", "bpm", "release_date", "from", "is_new"}

# 校验下限：曲库只有变多没有变少（旧曲不会下架），低于这个数一定是坏响应。
MIN_EXPECTED_SONGS = 1000

UA = "liz-bot-music-data-fetcher/1.0"


def _read_etag(etag_path: Path) -> str | None:
    try:
        value = etag_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _request(url: str, etag: str | None) -> tuple[int, bytes, str | None]:
    """发一次 GET。返回 (状态码, body, 新 ETag)。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if etag:
        req.add_header("If-None-Match", etag)

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read(), resp.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        # 304 走的是异常分支（urlopen 把非 2xx 都当异常）
        if exc.code == 304:
            return 304, b"", etag
        raise


def validate(raw: bytes) -> list:
    """解析并校验响应，返回乐曲列表。任何问题都抛 ValueError。"""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"响应不是合法 JSON：{exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"顶层应为数组，实际是 {type(data).__name__}")

    if len(data) < MIN_EXPECTED_SONGS:
        raise ValueError(
            f"乐曲数仅 {len(data)}，低于下限 {MIN_EXPECTED_SONGS} —— 疑似截断或限流"
        )

    for idx, song in enumerate(data):
        if not isinstance(song, dict):
            raise ValueError(f"第 {idx} 项不是对象")
        missing = REQUIRED_SONG_KEYS - song.keys()
        if missing:
            raise ValueError(f"第 {idx} 项（id={song.get('id')}）缺少字段：{sorted(missing)}")
        basic_missing = REQUIRED_BASIC_KEYS - song["basic_info"].keys()
        if basic_missing:
            raise ValueError(
                f"第 {idx} 项（id={song.get('id')}）basic_info 缺少字段：{sorted(basic_missing)}"
            )
        try:
            int(song["id"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {idx} 项 id 不是整数：{song.get('id')!r}") from exc
        if not isinstance(song["ds"], list) or len(song["ds"]) != len(song["charts"]):
            raise ValueError(
                f"第 {idx} 项（id={song.get('id')}）ds 与 charts 长度不一致："
                f"{len(song['ds'])} vs {len(song['charts'])}"
            )

    return data


def atomic_write(target: Path, raw: bytes) -> None:
    """先写同目录临时文件再 replace，避免中途失败留下半个文件。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".music_data.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="拉取水鱼曲库到本地")
    parser.add_argument("--url", default=DEFAULT_URL, help="数据源地址")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出文件路径")
    parser.add_argument("--force", action="store_true", help="忽略 ETag，强制重新下载")
    args = parser.parse_args(argv)

    target = Path(args.out).resolve()
    etag_path = target.with_suffix(target.suffix + ".etag")
    etag = None if args.force else _read_etag(etag_path)

    print(f"数据源：{args.url}")
    print(f"输出：  {target}")
    print(f"缓存：  {'强制重新下载' if args.force else (f'ETag {etag[:24]}…' if etag else '无（首次）')}")

    try:
        status, body, new_etag = _request(args.url, etag)
    except urllib.error.URLError as exc:
        print(f"\n✗ 请求失败：{exc}", file=sys.stderr)
        return 1

    if status == 304:
        print("\n= 曲库未变化（304），本地文件保持原样")
        if target.exists():
            size = target.stat().st_size
            print(f"  当前文件：{size:,} 字节")
        else:
            # 有 ETag 缓存却没有数据文件 —— 缓存与实际不一致，需要重下
            print("  ⚠ 有 ETag 缓存但数据文件不存在，改为强制下载")
            return main([*filter(lambda a: a != "--force", argv or []), "--force"])
        return 0

    try:
        songs = validate(body)
    except ValueError as exc:
        print(f"\n✗ 响应校验未通过，**未覆盖**本地文件：{exc}", file=sys.stderr)
        return 2

    atomic_write(target, body)
    if new_etag:
        etag_path.write_text(new_etag, encoding="utf-8")

    dx = sum(1 for s in songs if s["type"] == "DX")
    sd = sum(1 for s in songs if s["type"] == "SD")
    charts = sum(len(s["charts"]) for s in songs)
    utage = sum(1 for s in songs if int(s["id"]) >= 100000)

    print(f"\n✓ 已写入 {target}")
    print(f"  乐曲 {len(songs)} 首（DX {dx} / SD {sd}，其中宴会场 {utage}）")
    print(f"  谱面 {charts} 张")
    print(f"  文件 {target.stat().st_size:,} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
