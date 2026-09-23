"""曲库数据路径 —— 供查歌等模块共享。

原先该路径定义在 command_handler.py 中，拆分后集中放这里，
避免每个模块各写一份 os.path 拼接。

两个只读数据源
--------------
============================  ==========================================
``MUSIC_DATA_JSON``           水鱼（diving-fish）曲库，含曲名 / 定数 / 谱师
``ALIASES_JSON``              柚子（yuzuchan）别名库，含每首曲的别名列表
============================  ==========================================

两者都**只读**、都随镜像分发，分别由 ``_tools/fetch_music_data.py`` 与
``_tools/fetch_aliases.py`` 拉取。两个数据源用同一套曲目 ID 关联
（DX 为 ``id + 10000``），可直接对表。

.. note::
   这里原先还导出 ``ALIAS_JSON``，指向旧曲库目录下那个**会被运行时写入**的
   ``alias.json``，因此需要单独路由到持久化卷。该文件（以曲名为主键、
   数据陈旧）已归档到 ``_backup/liz_bot_song_db_2026-09-23/``；
   现行别名库改用 ``ALIASES_JSON``，以 ``song_id`` 为主键，且**只读** ——
   运行时不再写任何曲库文件。
"""

import os

from liz_bot.runtime_paths import ALIAS_FILE_PATH, SONG_FILE_PATH

# liz_bot/ 目录（保留旧名，兼容可能的外部引用）
current_dir = os.path.dirname(os.path.abspath(__file__))

# 曲库数据文件完整路径（水鱼 music_data.json）
MUSIC_DATA_JSON = os.path.join(SONG_FILE_PATH, "music_data.json")

# 别名库文件完整路径（柚子 aliases.json）
ALIASES_JSON = os.path.join(ALIAS_FILE_PATH, "aliases.json")

__all__ = [
    "current_dir",
    "SONG_FILE_PATH",
    "ALIAS_FILE_PATH",
    "MUSIC_DATA_JSON",
    "ALIASES_JSON",
    "song_file",
]


def song_file(name: str) -> str:
    """返回曲库目录下某个文件的完整路径。

    :param name: 文件名，如 "music_data.json"
    :return: 完整路径
    """
    return os.path.join(SONG_FILE_PATH, name)
