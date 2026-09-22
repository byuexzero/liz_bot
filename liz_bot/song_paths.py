"""曲库数据路径 —— 供查歌 / 别名管理等模块共享。

原先该路径定义在 command_handler.py 中，拆分后集中放这里，
避免每个模块各写一份 os.path 拼接。

只读 / 可写之分
---------------
``songs.json`` 等文件是**只读**的，始终位于仓库内（随镜像分发）。
``alias.json`` 是**唯一会被运行时写入**的曲库文件，因此在挂载了持久化卷的
部署中它位于卷上 —— 具体解析逻辑见 :mod:`liz_bot.runtime_paths`，
本模块只负责对外提供统一的常量名。
"""

import os

from liz_bot.runtime_paths import ALIAS_JSON, SONG_FILE_PATH

# liz_bot/ 目录（保留旧名，兼容可能的外部引用）
current_dir = os.path.dirname(os.path.abspath(__file__))

# 常用数据文件完整路径
SONGS_JSON = os.path.join(SONG_FILE_PATH, "songs.json")

# ALIAS_JSON 由 runtime_paths 解析后在此重新导出，
# 使 `from liz_bot.song_paths import ALIAS_JSON` 的既有写法继续可用。
__all__ = [
    "current_dir",
    "SONG_FILE_PATH",
    "SONGS_JSON",
    "ALIAS_JSON",
    "song_file",
]


def song_file(name: str) -> str:
    """返回曲库目录下某个文件的完整路径。

    ``alias.json`` 是特例：它是唯一可写的曲库文件，挂载数据卷时不在仓库内，
    因此单独路由到 :data:`ALIAS_JSON`，避免调用方拿到只读的基线副本。

    :param name: 文件名，如 "songs.json"
    :return: 完整路径
    """
    if name == "alias.json":
        return ALIAS_JSON
    return os.path.join(SONG_FILE_PATH, name)
