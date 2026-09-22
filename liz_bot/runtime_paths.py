"""运行时数据目录 —— 把「会被写入」的路径集中到一处，便于挂载持久化卷。

为什么需要
----------
机器人只有三处会在**运行时写入磁盘**：

==================  ==============================  ====================
路径                写入方                           内容
==================  ==============================  ====================
``alias.json``      ``song_alias.add_song_alias``   用户新增的歌曲别名
``ai_chat/``        ``qqgroup-ai-bot``              AI 会话历史
``bot_log/``        ``qqgroupbot``                  botpy 运行日志
==================  ==============================  ====================

而 ``songs.json`` / ``songs_cn.json`` / ``tags.json`` 等曲库文件是**只读**的，
随镜像一起分发即可，不需要也不应该放到卷上。

为什么必须可配置
----------------
在容器平台（Sealos / Render / Fly.io 等）上，容器内文件系统默认是
**临时的**：重新部署、重启、容器被回收，都会把这些写入清空。用户辛苦攒下的
别名会莫名其妙消失，而且**没有任何报错** —— 最难排查的那类问题。

解法是挂一个持久化卷，再把上面三处路径指过去。

用法
----
设置环境变量 ``LIZ_DATA_DIR`` 指向卷的挂载点（例如 ``/data``）::

    LIZ_DATA_DIR=/data

路径随之变为 ``/data/alias.json``、``/data/ai_chat/``、``/data/bot_log/``。

**未设置该变量时一切照旧**（沿用仓库内目录），本地开发零影响 ——
这也是刻意保留 ``LIZ_DATA_DIR`` 默认值而非强制必填的原因。

首次启动播种
------------
挂载一个空卷后，卷上并没有 ``alias.json``，如果直接读会得到「别名全空」。
因此 :func:`ensure_dirs` 会在卷上缺少该文件时，把**镜像内的基线副本**复制
过去。之后卷上的版本始终优先，绝不会被镜像覆盖 —— 用户的增量是安全的。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# liz_bot/ 与项目根目录（用 __file__ 推导，不依赖 cwd）
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent

# ---------------------------------------------------------------------------
# 只读基线：始终位于镜像内，不受 LIZ_DATA_DIR 影响
# ---------------------------------------------------------------------------

# 曲库数据目录。songs.json 等只读文件都从这里读。
SONG_FILE_PATH = str(_HERE / "maimaiDX_songs")

# 随镜像分发的基线别名表，用于首次播种到空卷
BASELINE_ALIAS_JSON = str(_HERE / "maimaiDX_songs" / "alias.json")

# ---------------------------------------------------------------------------
# 数据根目录
# ---------------------------------------------------------------------------

_raw_data_dir = (os.environ.get("LIZ_DATA_DIR") or "").strip()

#: 持久化数据卷的挂载点；未配置时为 ``None``（表示沿用仓库内目录）。
DATA_ROOT: str | None = (
    str(Path(_raw_data_dir).expanduser().resolve()) if _raw_data_dir else None
)


def _pick(volume_relative: str, repo_default: Path) -> str:
    """有数据卷时返回卷内路径，否则沿用仓库内路径。"""
    if DATA_ROOT is None:
        return str(repo_default)
    return str(Path(DATA_ROOT) / volume_relative)


#: 别名表。**唯一会被写入的曲库文件**，因此是挂卷时最该保住的东西。
ALIAS_JSON = _pick("alias.json", _HERE / "maimaiDX_songs" / "alias.json")

#: AI 会话记录目录（仅 ``qqgroup-ai-bot.py`` 使用）。
AI_CHAT_DIR = _pick("ai_chat", _HERE / "ai_chat")

#: botpy 日志目录。
LOG_DIR = _pick("bot_log", _REPO_ROOT / "bot_log")


def is_volume_backed() -> bool:
    """数据是否落在持久化卷上（即是否设置了 ``LIZ_DATA_DIR``）。"""
    return DATA_ROOT is not None


def describe() -> list[str]:
    """返回当前路径解析结果，供启动日志打印。"""
    origin = f"数据卷 {DATA_ROOT}" if DATA_ROOT is not None else "仓库内目录（未设 LIZ_DATA_DIR）"
    return [
        f"数据来源：{origin}",
        f"别名表：{ALIAS_JSON}",
        f"AI 会话：{AI_CHAT_DIR}",
        f"日志目录：{LOG_DIR}",
        f"曲库基线：{SONG_FILE_PATH}",
    ]


def ensure_dirs() -> list[str]:
    """创建可写目录，并在空卷上播种基线别名表。

    :return: 人类可读的结果说明，逐条打印到启动日志。
    """
    notes: list[str] = []

    # 1. 数据卷挂载点本身。LIZ_DATA_DIR 指向不存在的路径时（例如填错），
    #    这里会顺手创建，而不是等到写 alias.json 时才报错。
    if DATA_ROOT is not None:
        try:
            os.makedirs(DATA_ROOT, exist_ok=True)
            notes.append(f"数据卷挂载点就绪：{DATA_ROOT}")
        except OSError as exc:
            notes.append(f"⚠ 数据卷挂载点创建失败：{DATA_ROOT}（{exc}）")

    # 2. 两个可写目录
    for label, path in (("AI 会话目录", AI_CHAT_DIR), ("日志目录", LOG_DIR)):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            notes.append(f"⚠ {label}创建失败：{path}（{exc}）")

    # 3. 空卷播种：只在卷上确实没有 alias.json 时才复制一次
    if DATA_ROOT is not None and not os.path.exists(ALIAS_JSON):
        try:
            shutil.copyfile(BASELINE_ALIAS_JSON, ALIAS_JSON)
            notes.append(f"空卷首次启动，已播种基线别名表 → {ALIAS_JSON}")
        except OSError as exc:
            # 播种失败不该阻止启动：新增别名仍可写入，只是初始为空
            notes.append(f"⚠ 别名表播种失败：{exc}（初始别名将为空）")

    return notes
