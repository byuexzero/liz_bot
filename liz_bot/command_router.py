"""指令入口层 —— 解析与分发。

本模块只负责「把消息变成指令名+参数」和「把指令名路由到对应业务模块」，
不含任何业务逻辑。具体功能实现见：
    song_query.py    查歌 + 别名查询
    song_alias.py    别名管理 —— **写入已停用，仅保留接口**
    daily_funcs.py   随机数 / 问候 / 帮助

注：``/qr``（扫码查询）已**暂时移除**——它是对舞萌服务端的发包功能。
恢复步骤见 ``liz_bot/_已移除功能_舞萌发包.md``。

注：``/bm``、``/别名查歌``、``/查询别名``、``/cbm`` 现走**柚子（yuzuchan）**
别名库，数据见 ``liz_bot/yuzuchan_aliases/aliases.json``（由
``_tools/fetch_aliases.py`` 拉取）。

注：``/添加别名`` 仍**已停用** —— 别名库是只读快照，下次拉取就会覆盖；
上游是玩家众包投票制，本地写会与之分叉。

注：本模块**不再硬编码任何用户可见文案**（「未知指令」等），全部来自
``liz_bot/texts/replies.json``，见 :mod:`liz_bot.replies`。
"""

import re

from liz_bot import daily_funcs, replies, song_alias, song_query


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


def _keyword_variants(cmd_params):
    """关键词候选，按「完整参数串 → 首 token」的顺序。

    为什么不能只取首 token
    ----------------------
    ``parse_command`` 是按空格切分的，只取 ``cmd_params[0]`` 会让**含空格的
    关键词永远查不到**。而这类关键词很多：实测柚子别名库 9853 条别名里有
    **672 条（6.8%）含空格**（``true love song``、``color my world`` …），
    曲名同理（``True Love Song``、``7thSense`` 之外的一堆英文名）。

    先试整串、再退回首 token，因此是旧行为的**超集**：
    ``/song 8 extra`` 这类脏输入仍能退回首 token 命中 id 8。
    """
    if not cmd_params:
        return []
    joined = " ".join(cmd_params).strip()
    if joined and joined != cmd_params[0]:
        return [joined, cmd_params[0]]
    return [cmd_params[0]]


def _reply_variants(cmd_params, reply_fn, miss):
    """依次用候选关键词调用 ``reply_fn``，返回首个不等于 ``miss`` 的结果。

    :param reply_fn: 接受单个关键词、返回字符串的函数
    :param miss: 「未命中」的哨兵返回值
    """
    for kw in _keyword_variants(cmd_params):
        result = reply_fn(kw)
        if result != miss:
            return result
    return miss


async def handle_command(cmd_name, cmd_params):
    """
    根据解析出的指令名，执行对应逻辑
    :param cmd_name: 指令名字符串
    :param cmd_params: 指令参数
    :return reply str
    """
    keyword = _first_param(cmd_params)

    # 文案一律从 replies.json 取。``miss`` 先取一次：它既作「未命中」返回值，
    # 又作 _reply_variants 的哨兵 —— 取一次可保证「比较用的值」与
    # 「返回的值」是同一个字符串（否则热更新恰好在两次取值之间生效时会误判）。
    miss = replies.text("song.not_found")

    # ---------- 基础指令 ----------
    if cmd_name == "help":
        return daily_funcs.help_reply()

    if cmd_name == "hello":
        return daily_funcs.greeting_reply()

    if cmd_name == "random":
        return daily_funcs.random_int_from_list(cmd_params)

    # ---------- 查歌 ----------
    # 纯数字关键词的指令（id / songdata）不需要多词候选，直接用首参数。
    if cmd_name in ("id", "id查歌", "songid"):
        if keyword is None:
            return miss
        return song_query.song_reply(keyword, song_query.BY_ID)

    if cmd_name == "songdata":
        if keyword is None:
            return replies.text("song.data_error")
        return song_query.songdata_reply(keyword)

    # 以下四类关键词都可能含空格（曲名 / 别名），故走多词候选。
    if cmd_name in ("bm", "别名查歌", "songalias"):
        if keyword is None:
            return miss
        return _reply_variants(
            cmd_params,
            lambda kw: song_query.song_reply(kw, song_query.BY_ALIAS),
            miss)

    if cmd_name in ("name", "歌名查歌", "songname"):
        if keyword is None:
            return miss
        return _reply_variants(
            cmd_params,
            lambda kw: song_query.song_reply(kw, song_query.BY_NAME),
            miss)

    if cmd_name == "song":
        if keyword is None:
            return miss
        return _reply_variants(
            cmd_params,
            lambda kw: song_query.song_reply(kw, song_query.BY_ANY),
            miss)

    # ---------- 别名管理 ----------
    if cmd_name in ("查询别名", "cbm"):
        if keyword is None:
            return replies.text("song.bad_params")
        return _reply_variants(cmd_params, song_query.alias_reply, miss)

    if cmd_name in ("添加别名", "addalias"):
        # 写入已停用：别名库是只读快照，写会与上游众包投票分叉。
        # 参数校验保持原样，以便日后恢复时调用方无需改动。
        if keyword is None or len(cmd_params) < 2:
            return replies.text("alias.add_bad_params")
        return song_alias.add_alias_reply(keyword, cmd_params[1])

    # ---------- 扫码 ----------
    # 已暂时移除：/qr 会对舞萌服务端发包（POST 到 ai.sys-allnet.cn）。
    # 恢复方法见 liz_bot/_已移除功能_舞萌发包.md。

    # ---------- 未知指令 ----------
    return replies.text("router.unknown_command", cmd_name=cmd_name)
