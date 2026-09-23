"""从柚子（yuzuchan）拉取全量歌曲别名到本地。

数据源
------
``GET https://www.yuzuchan.moe/api/v2/aliases/maimaidx/aliases``
免鉴权，一次返回全部曲目的别名。

为什么是柚子而不是水鱼
----------------------
水鱼 ``maimaidx-prober`` **不提供歌曲别名**（它的 ``alias`` 只出现在
"查询参数别名"里）。社区别名由柚子单独维护，且是**众包投票**出来的：
玩家提交申请 → 群友投票 → 过审入库。参考实现见
``Diving-Fish/nonebot-plugin-maimaidx``。

ID 空间
-------
柚子的 ``song_id`` 与水鱼 ``music_data.json`` 的 ``id`` **完全一致**
（SD 为原始 id，DX 为 ``id + 10000``，宴会场 ≥ 100000），可直接关联，
不需要偏移。注意落雪（lxns）用的是另一套空间（需要 ``+10000`` 换算），
本脚本不涉及。

文件格式
--------
落盘内容就是上游返回的原始数组，形如::

    [
      {"song_id": 8, "name": "True Love Song", "is_votable": true,
       "alias": ["true love song", "会员制餐厅", "真的爱情歌", ...]},
      ...
    ]

``name`` 是曲名，``alias`` 是别名列表（多为小写），``is_votable`` 表示
该曲是否还接受新别名投票。

先校验再落盘
------------
与 ``fetch_music_data.py`` 同一策略：响应可能是网关错误页或限流提示，
直接覆盖会把好数据毁掉。因此先完整解析 + 结构校验，全部通过才原子替换。

关于缓存（实测结论）
--------------------
**上游不发 ``ETag`` / ``Last-Modified`` / ``Cache-Control``**
（水鱼的 ``music_data`` 三个都有，柚子一个都没有），所以 304 协商
在这里**实际不会命中**，每次运行都是全量下载约 250 KB。

ETag 那套代码保留着 —— 万一上游日后补上就自动生效。为了在当下也有
"重复运行无副作用"的效果，下载后会**先和本地文件逐字节比较**：
内容一致就报"未变化"并直接返回，不改写文件、不动 mtime，
这样 ``git status`` 不会出现无意义的改动。

用法::

    python _tools/fetch_aliases.py                # 增量（带 ETag）
    python _tools/fetch_aliases.py --force        # 强制重新下载
    python _tools/fetch_aliases.py --url <地址>   # 换数据源（如 .cn 域名）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# .moe 与 .cn 是同一服务的两个域名，内容一致；默认用 .moe。
DEFAULT_URL = "https://www.yuzuchan.moe/api/v2/aliases/maimaidx/aliases"

# 相对本文件定位，避免依赖 cwd
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = _REPO_ROOT / "liz_bot" / "yuzuchan_aliases" / "aliases.json"

# 曲库文件，仅用于关联性自检（缺失时跳过）
MUSIC_DATA_JSON = _REPO_ROOT / "liz_bot" / "divingfish_songs" / "music_data.json"

# 每条记录必须具备的字段。少一个就说明上游结构变了，宁可不更新。
REQUIRED_KEYS = {"song_id", "name", "is_votable", "alias"}

# 校验下限：别名库只有变多没有变少，低于这个数一定是坏响应。
MIN_EXPECTED_ALIASES = 1000

# 关联覆盖率告警线。正常情况是 100%（实测 1394/1394）；
# 明显偏低通常意味着上游换了 ID 空间（就像落雪那套 +10000）。
MIN_JOIN_COVERAGE = 0.90

UA = "liz-bot-alias-fetcher/1.0"

# 实测上游会偶发 522（Cloudflare 回源超时）与数十秒的长时间无响应，
# 所以对"可能只是抖动"的错误做几次退避重试。
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 5  # 秒；每次翻倍（5 / 10）


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


def _request_with_retry(url: str, etag: str | None,
                        attempts: int = RETRY_ATTEMPTS) -> tuple[int, bytes, str | None]:
    """带退避重试的 GET。

    只重试"可能只是抖动"的错误：5xx、429、以及连接层异常。
    4xx（除 429）是确定性错误，重试没有意义，直接抛出去。
    """
    last: Exception | None = None
    for n in range(1, attempts + 1):
        try:
            return _request(url, etag)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise
            last = exc
        except urllib.error.URLError as exc:
            last = exc

        if n < attempts:
            wait = RETRY_BACKOFF * (2 ** (n - 1))
            print(f"  ⚠ 第 {n} 次请求失败（{last}），{wait} 秒后重试…", file=sys.stderr)
            time.sleep(wait)

    assert last is not None
    raise last


def validate(raw: bytes) -> list:
    """解析并校验响应，返回别名记录列表。任何问题都抛 ValueError。"""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"响应不是合法 JSON：{exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"顶层应为数组，实际是 {type(data).__name__}")

    if len(data) < MIN_EXPECTED_ALIASES:
        raise ValueError(
            f"别名记录仅 {len(data)} 条，低于下限 {MIN_EXPECTED_ALIASES} —— 疑似截断或限流"
        )

    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx} 项不是对象")
        missing = REQUIRED_KEYS - item.keys()
        if missing:
            raise ValueError(f"第 {idx} 项缺少字段：{sorted(missing)}")
        try:
            int(item["song_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {idx} 项 song_id 不是整数：{item['song_id']!r}") from exc
        if not isinstance(item["alias"], list):
            raise ValueError(
                f"第 {idx} 项（song_id={item['song_id']}）alias 不是数组："
                f"{type(item['alias']).__name__}"
            )
        bad = [a for a in item["alias"] if not isinstance(a, str)]
        if bad:
            raise ValueError(
                f"第 {idx} 项（song_id={item['song_id']}）alias 含非字符串项：{bad[:3]}"
            )

    return data


def check_join(records: list) -> tuple[int, int, list[int]]:
    """关联性自检：别名里的 song_id 能否对上曲库。

    :return: ``(曲库总数, 命中数, 曲库中缺别名的 id 列表)``；
             曲库文件不存在时返回 ``(0, 0, [])``。
    """
    if not MUSIC_DATA_JSON.exists():
        return 0, 0, []

    with open(MUSIC_DATA_JSON, "r", encoding="utf-8") as fh:
        songs = json.load(fh)

    song_ids = {int(s["id"]) for s in songs if isinstance(s, dict) and "id" in s}
    alias_ids = {int(r["song_id"]) for r in records}

    missing = sorted(song_ids - alias_ids)
    return len(song_ids), len(song_ids & alias_ids), missing


def atomic_write(target: Path, raw: bytes) -> None:
    """先写同目录临时文件再 replace，避免中途失败留下半个文件。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".aliases.", suffix=".tmp")
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
    parser = argparse.ArgumentParser(description="拉取柚子别名库到本地")
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
        status, body, new_etag = _request_with_retry(args.url, etag)
    except urllib.error.URLError as exc:
        print(f"\n✗ 请求失败（已重试 {RETRY_ATTEMPTS} 次）：{exc}", file=sys.stderr)
        print("  本地文件未改动。上游偶发 522，稍后重跑通常即可。", file=sys.stderr)
        return 1

    if status == 304:
        print("\n= 别名库未变化（304），本地文件保持原样")
        if target.exists():
            print(f"  当前文件：{target.stat().st_size:,} 字节")
        else:
            # 有 ETag 缓存却没有数据文件 —— 缓存与实际不一致，需要重下
            print("  ⚠ 有 ETag 缓存但数据文件不存在，改为强制下载")
            return main([*filter(lambda a: a != "--force", argv or []), "--force"])
        return 0

    try:
        records = validate(body)
    except ValueError as exc:
        print(f"\n✗ 响应校验未通过，**未覆盖**本地文件：{exc}", file=sys.stderr)
        return 2

    # 上游没有 ETag，304 协商命中不了。这里用内容比对达到同样的效果：
    # 一致就不改写，避免 mtime 抖动与无意义的 git 改动。
    # ``--force`` 的语义是"下载并写入"，因此跳过这个短路。
    if target.exists() and not args.force:
        try:
            if target.read_bytes() == body:
                print("\n= 别名库内容与本地一致，未改写文件")
                print(f"  {len(records)} 首 / {target.stat().st_size:,} 字节")
                return 0
        except OSError:
            pass

    atomic_write(target, body)
    if new_etag:
        etag_path.write_text(new_etag, encoding="utf-8")

    alias_total = sum(len(r["alias"]) for r in records)
    no_alias = sum(1 for r in records if not r["alias"])

    print(f"\n✓ 已写入 {target}")
    print(f"  曲目 {len(records)} 首，别名 {alias_total} 条（平均 {alias_total / len(records):.1f} 条/首）")
    print(f"  无别名的曲目：{no_alias} 首")
    print(f"  文件 {target.stat().st_size:,} 字节")

    # ---- 关联性自检（不阻断写入，只告警）----------------------------------
    try:
        song_count, joined, missing = check_join(records)
    except (OSError, ValueError, KeyError) as exc:
        print(f"  ⚠ 关联自检跳过：{exc}")
        return 0

    if song_count == 0:
        print("  ⚠ 未找到曲库文件，跳过关联自检（先跑 fetch_music_data.py 可启用）")
        return 0

    coverage = joined / song_count
    print(f"  关联曲库：{joined}/{song_count}（{coverage:.1%}）")
    if missing:
        preview = ", ".join(str(i) for i in missing[:10])
        print(f"    ⚠ 曲库中 {len(missing)} 首没有别名，前几个：{preview}")
    if coverage < MIN_JOIN_COVERAGE:
        print(
            f"\n⚠ 关联覆盖率 {coverage:.1%} 低于 {MIN_JOIN_COVERAGE:.0%} ——"
            " 上游可能换了 ID 空间（对照 lxns 的 +10000 换算），请人工确认。",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
