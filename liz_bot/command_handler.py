"""指令处理 —— 兼容层（facade）。

业务逻辑已按职责拆分到以下模块：

    song_paths.py       曲库数据路径
    song_query.py       查歌（数据源：水鱼 music_data.json）
    judge_detail.py     谱面判定细节 —— 物量 + 各判定档位的扣分（``/songdata``）
    song_alias.py       别名管理 —— **已暂时移除实现，仅保留接口**
    daily_funcs.py      随机数 / 问候
    command_router.py   指令表 / 解析 / 参数校验 / 分发 / 帮助

本文件保留原有导入路径，使 `from liz_bot.command_handler import handle_command, parse_command`
等既有用法继续可用。新代码建议直接从上述模块导入。

注：``reply_text`` 是 bot 外壳的唯一入口（识别 ``/`` 与 ``#`` 两种前缀），
这里一并转出。

注：``#`` 前缀的**舞萌命名空间**已建立（``parse_maimai_command`` /
``is_maimai_command`` / ``handle_maimai_command``），但**实现尚未接入** ——
所有 ``#`` 指令统一回复「未解析的指令」。接入点见
``command_router.handle_maimai_command`` 的 docstring。

注：``qr_reply``（扫码查询，对舞萌服务端发包）已**暂时移除**。
它依赖的服务端 API 工具链**不随仓库分发**（见 ``.gitignore``）——
那是一个功能完整的上传工具，公开分发不合适；需要时从本地获取。

注：别名功能（查别名 / 加别名）已**暂时移除**，相关接口保留但返回空结果。
原始实现与数据归档在 ``_backup/liz_bot_song_db_2026-09-23/``。

注：``songdata_reply``（原样 dump 内部结构，调试用）已**被替换**为
``judge_detail.judge_detail_reply`` —— 输出「某谱面某难度的物量 + 各判定扣分」，
需要 ``(歌曲id, 难度)`` 两个参数（2026-09-25）。
"""

# 兼容旧路径：原先这些名字都定义在 command_handler 中
from liz_bot.command_router import (
    COMMANDS,
    Command,
    handle_command,
    handle_maimai_command,
    help_reply,
    is_maimai_command,
    parse_command,
    parse_maimai_command,
    pending_store,
    reply_text,
)
from liz_bot.judge_detail import judge_detail_reply
from liz_bot.song_paths import SONG_FILE_PATH, song_file
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
)
from liz_bot.song_alias import add_alias_reply, add_song_alias
from liz_bot.daily_funcs import (
    greeting_reply,
    random_int_from_list,
)

# 旧代码里以 `song_file_path` 变量名引用曲库目录，此处一并保留
song_file_path = SONG_FILE_PATH

__all__ = [
    # 入口层
    "handle_command",
    "parse_command",
    "reply_text",
    "help_reply",
    "COMMANDS",
    "Command",
    # 多轮补参（见 liz_bot/pending.py）
    "pending_store",
    # 舞萌命名空间（`#` 前缀）—— 实现尚未接入，统一回「未解析的指令」
    "parse_maimai_command",
    "is_maimai_command",
    "handle_maimai_command",
    # 曲库路径
    "song_file_path",
    "SONG_FILE_PATH",
    "song_file",
    # 查歌
    "select_song",
    "query_by_id",
    "query_by_name",
    "query_by_alias",
    "query_any",
    "format_song",
    "song_reply",
    "alias_reply",
    # 谱面判定细节（/songdata 的新实现，取代 songdata_reply）
    "judge_detail_reply",
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
]


if __name__ == "__main__":
    # 手动冒烟：查一首曲目并打印格式化结果
    print(song_reply("8", BY_ID))
