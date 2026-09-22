"""指令入口层 —— 解析与分发。

本模块只负责「把消息变成指令名+参数」和「把指令名路由到对应业务模块」，
不含任何业务逻辑。具体功能实现见：
    song_query.py    查歌 / 查别名
    song_alias.py    添加别名
    daily_funcs.py   随机数 / 问候 / 帮助

注：``/qr``（扫码查询）已**暂时移除**——它是对舞萌服务端的发包功能。
恢复步骤见 ``liz_bot/_已移除功能_舞萌发包.md``。
"""

import re

from liz_bot import daily_funcs, song_alias, song_query


async def parse_command(message_content: str):
    """
    解析指令：/指令名 参数1 参数2 → 返回(指令名, [参数1, 参数2], True)
    无参数时返回(指令名, [], True)，非指令格式返回(None, None, False)
    """
    content = message_content.strip()
    # 基础正则：匹配 /指令名 开头，后接任意参数（按空格分割）
    command_pattern = re.compile(r'^/(\w+)(?:\s+(.*))?$')
    match = command_pattern.match(content)

    if not match:
        return None, None, False

    cmd_name = match.group(1)
    cmd_params = []
    params_str = match.group(2)  # 所有参数的原始字符串

    if params_str:
        # 极简处理：仅按空格分割参数（不处理引号，纯空格分割）
        cmd_params = params_str.split()

    return cmd_name, cmd_params, True


def _first_param(cmd_params):
    """取首个参数，缺失时返回 None（供各分支做参数校验）。"""
    return cmd_params[0] if cmd_params else None


async def handle_command(cmd_name, cmd_params):
    """
    根据解析出的指令名，执行对应逻辑
    :param cmd_name: 指令名字符串
    :param cmd_params: 指令参数
    :return reply str
    """
    keyword = _first_param(cmd_params)

    # ---------- 基础指令 ----------
    if cmd_name == "help":
        return daily_funcs.help_reply()

    if cmd_name == "hello":
        return daily_funcs.greeting_reply()

    if cmd_name == "random":
        return daily_funcs.random_int_from_list(cmd_params)

    # ---------- 查歌 ----------
    if cmd_name in ("id", "id查歌", "songid"):
        if keyword is None:
            return song_query.NOT_FOUND
        return song_query.song_reply(keyword, song_query.BY_ID)

    if cmd_name in ("bm", "别名查歌", "songalias"):
        if keyword is None:
            return song_query.NOT_FOUND
        return song_query.song_reply(keyword, song_query.BY_ALIAS)

    if cmd_name in ("name", "歌名查歌", "songname"):
        if keyword is None:
            return song_query.NOT_FOUND
        return song_query.song_reply(keyword, song_query.BY_NAME)

    if cmd_name == "song":
        if keyword is None:
            return song_query.NOT_FOUND
        return song_query.song_reply(keyword, song_query.BY_ANY)

    if cmd_name == "songdata":
        if keyword is None:
            return "DataError"
        return song_query.songdata_reply(keyword)

    # ---------- 别名管理 ----------
    if cmd_name in ("查询别名", "cbm"):
        if keyword is None:
            return "笨蛋是不是没写参数啊"
        return song_query.alias_reply(keyword)

    if cmd_name in ("添加别名", "addalias"):
        if keyword is None or len(cmd_params) < 2:
            return song_alias.ADD_BAD_PARAMS
        return song_alias.add_alias_reply(keyword, cmd_params[1])

    # ---------- 扫码 ----------
    # 已暂时移除：/qr 会对舞萌服务端发包（POST 到 ai.sys-allnet.cn）。
    # 恢复方法见 liz_bot/_已移除功能_舞萌发包.md。

    # ---------- 未知指令 ----------
    return f"未知指令：{cmd_name}"
