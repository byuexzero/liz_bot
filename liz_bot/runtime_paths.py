"""运行时数据目录 —— 把「会被写入」的路径集中到一处，便于挂载持久化卷。

为什么需要
----------
机器人只有两处会在**运行时写入磁盘**：

===========  ==================  ================
路径         写入方               内容
===========  ==================  ================
``ai_chat/``  ``qqgroup-ai-bot``  AI 会话历史
``bot_log/``  ``qqgroupbot``      botpy 运行日志
===========  ==================  ================

而 ``music_data.json`` / ``aliases.json`` / ``texts/replies.json`` 这些
曲库、别名库与回复文本文件都是**只读**的，随镜像一起分发即可，
不需要也不应该放到卷上。

.. note::
   这里原先还有第三项 ``alias.json``（用户新增的歌曲别名，位于旧曲库目录）。
   它曾是需要持久化的可写数据，因此有"空卷播种"逻辑。现已改为
   **只读的柚子别名库**（``aliases.json``，随镜像分发、运行时不再写入），
   故路径常量与播种逻辑一并删除。旧实现与数据文件归档在
   ``_backup/liz_bot_song_db_2026-09-23/``。

为什么必须可配置
----------------
在容器平台（Sealos / Render / Fly.io 等）上，容器内文件系统默认是
**临时的**：重新部署、重启、容器被回收，都会把这些写入清空。用户与 AI
的对话历史会莫名其妙消失，而且**没有任何报错** —— 最难排查的那类问题。

解法是挂一个持久化卷，再把上面两处路径指过去。

用法
----
设置环境变量 ``LIZ_DATA_DIR`` 指向卷的挂载点（例如 ``/data``）::

    LIZ_DATA_DIR=/data

路径随之变为 ``/data/ai_chat/``、``/data/bot_log/``。

**未设置该变量时一切照旧**（沿用仓库内目录），本地开发零影响 ——
这也是刻意保留 ``LIZ_DATA_DIR`` 默认值而非强制必填的原因。
"""

from __future__ import annotations

import os
from pathlib import Path

# liz_bot/ 与项目根目录（用 __file__ 推导，不依赖 cwd）
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent

# ---------------------------------------------------------------------------
# 只读基线：始终位于镜像内，不受 LIZ_DATA_DIR 影响
# ---------------------------------------------------------------------------

# 曲库数据目录。music_data.json 等只读文件都从这里读。
# 数据由 _tools/fetch_music_data.py 从水鱼（diving-fish）公开接口拉取。
SONG_FILE_PATH = str(_HERE / "divingfish_songs")

# 别名库目录。aliases.json 从这里读。
# 数据由 _tools/fetch_aliases.py 从柚子（yuzuchan）公开接口拉取 ——
# 水鱼不提供歌曲别名，别名由柚子单独维护（玩家众包投票产生）。
ALIAS_FILE_PATH = str(_HERE / "yuzuchan_aliases")

# 回复文本目录。replies.json 从这里读（见 liz_bot/replies.py）。
# 用户可见的全部提示文案都集中在这个文件里，改文案不用碰代码、也不用重启。
# 放在这里而不是 liz_bot/config/，是因为 config/ 下的 yaml 被 .gitignore
# 排除（只放行 *.example.yaml）—— 而文本文件必须随仓库/镜像分发。
TEXT_FILE_PATH = str(_HERE / "texts")

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
        f"AI 会话：{AI_CHAT_DIR}",
        f"日志目录：{LOG_DIR}",
        f"曲库基线：{SONG_FILE_PATH}",
        f"别名基线：{ALIAS_FILE_PATH}",
        f"回复文本：{TEXT_FILE_PATH}",
    ]


def ensure_dirs() -> list[str]:
    """创建运行时所需的可写目录。

    :return: 人类可读的结果说明，逐条打印到启动日志。
    """
    notes: list[str] = []

    # 1. 数据卷挂载点本身。LIZ_DATA_DIR 指向不存在的路径时（例如填错），
    #    这里会顺手创建，而不是等到写日志时才报错。
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

    return notes
