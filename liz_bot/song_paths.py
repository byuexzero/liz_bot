"""曲库数据路径 —— 供查歌 / 别名管理等模块共享。

原先该路径定义在 command_handler.py 中，拆分后集中放这里，
避免每个模块各写一份 os.path 拼接。
"""

import os

# liz_bot/ 目录
current_dir = os.path.dirname(os.path.abspath(__file__))

# 曲库数据目录：liz_bot/maimaiDX_songs/
SONG_FILE_PATH = os.path.join(current_dir, "maimaiDX_songs")

# 常用数据文件完整路径
SONGS_JSON = os.path.join(SONG_FILE_PATH, "songs.json")
ALIAS_JSON = os.path.join(SONG_FILE_PATH, "alias.json")


def song_file(name: str) -> str:
    """返回曲库目录下某个文件的完整路径。

    :param name: 文件名，如 "songs.json"
    :return: 完整路径
    """
    return os.path.join(SONG_FILE_PATH, name)
