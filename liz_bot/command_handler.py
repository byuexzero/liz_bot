"""指令处理 —— 兼容层（facade）。

业务逻辑已按职责拆分到以下模块：

    song_paths.py       曲库数据路径
    song_query.py       查歌 / 查别名
    song_alias.py       添加别名
    daily_funcs.py      随机数 / 问候 / 帮助
    command_router.py   指令解析与分发

本文件保留原有导入路径，使 `from liz_bot.command_handler import handle_command, parse_command`
等既有用法继续可用。新代码建议直接从上述模块导入。

注：``qr_reply``（扫码查询，对舞萌服务端发包）已**暂时移除**。
恢复步骤见 ``liz_bot/_已移除功能_舞萌发包.md``。
"""

# 兼容旧路径：原先这些名字都定义在 command_handler 中
from liz_bot.command_router import handle_command, parse_command
from liz_bot.song_paths import ALIAS_JSON, SONG_FILE_PATH, song_file
from liz_bot.song_query import (
    BY_ALIAS,
    BY_ANY,
    BY_ID,
    BY_NAME,
    alias_reply,
    format_song,
    query_any,
    query_by_alias,
    query_by_id,
    query_by_name,
    select_song,
    song_reply,
    songdata_reply,
)
from liz_bot.song_alias import add_alias_reply, add_song_alias
from liz_bot.daily_funcs import (
    greeting_reply,
    help_reply,
    random_int_from_list,
)

# 旧代码里以 `song_file_path` 变量名引用曲库目录，此处一并保留
song_file_path = SONG_FILE_PATH

__all__ = [
    # 入口层
    "handle_command",
    "parse_command",
    # 曲库路径
    "song_file_path",
    "SONG_FILE_PATH",
    "ALIAS_JSON",
    "song_file",
    # 查歌
    "select_song",
    "query_by_id",
    "query_by_name",
    "query_by_alias",
    "query_any",
    "format_song",
    "song_reply",
    "songdata_reply",
    "alias_reply",
    "BY_ID",
    "BY_NAME",
    "BY_ALIAS",
    "BY_ANY",
    # 别名管理
    "add_song_alias",
    "add_alias_reply",
    # 日常功能
    "random_int_from_list",
    "greeting_reply",
    "help_reply",
]


if __name__ == "__main__":
    aaa = select_song('id8', 3)
    print(aaa)
