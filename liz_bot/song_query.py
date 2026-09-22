"""查歌功能 —— 按 ID / 歌名 / 别名检索曲库，并格式化为回复文本。

主要对外接口：
    select_song(select_data, select_type)  低层检索，返回原始结构
    query_by_id / query_by_name / query_by_alias / query_any
    song_reply / songdata_reply / alias_reply  直接产出可回复的字符串
"""

import ijson

from liz_bot.song_paths import ALIAS_JSON, SONGS_JSON


def select_song(select_data: str, select_type: int):
    """
    流式读取嵌套 JSON 对象中的数组数据
    :param select_data: 歌曲检索关键词
    :param select_type: 0为id检索, 1为name检索, 2为别名检索, 3为混合检索
    :return: 简化后的列表，每个元素为包含完整song信息和对应别名的字典
             格式：[{"song": 完整song对象, "aliases": 对应别名列表}, ...]
    """
    result_list = []  # 单列表存储结果，简化返回值结构
    alias_to_names_map = {}
    name_to_full_aliases_map = {}

    # 加载alias.json构建映射表
    if select_type in (2, 3):
        with open(ALIAS_JSON, 'rb') as f_alias:
            for item in ijson.items(f_alias, 'item'):
                song_name = item.get("name")
                song_aliases = item.get("alias", [])

                # 构建别名→歌曲名映射（检索用）
                for alias in song_aliases:
                    if alias not in alias_to_names_map:
                        alias_to_names_map[alias] = []
                    if song_name not in alias_to_names_map[alias]:
                        alias_to_names_map[alias].append(song_name)

                # 构建歌曲名→完整别名映射（返回用）
                if song_name in name_to_full_aliases_map:
                    combined_aliases = list(set(name_to_full_aliases_map[song_name] + song_aliases))
                    name_to_full_aliases_map[song_name] = combined_aliases
                else:
                    name_to_full_aliases_map[song_name] = song_aliases.copy()

    # 辅助函数：别名匹配判断
    def is_alias_match(song_name):
        target_song_names = alias_to_names_map.get(select_data, [])
        return song_name in target_song_names

    # 读取并匹配歌曲
    with open(SONGS_JSON, 'rb') as f_song:
        for song in ijson.items(f_song, 'item'):
            s_id, s_name, s_aliases = song.get("id"), str(song.get("name")), song.get("alias", [])
            match = False

            # 检索类型匹配
            if select_type == 0:
                try:
                    match = (s_id == int(select_data))
                except (ValueError, TypeError):
                    continue
            elif select_type == 1:
                match = (s_name == select_data)
            elif select_type == 2:
                match = is_alias_match(s_name)
            elif select_type == 3:
                # 第一步：先判断别名匹配
                match = is_alias_match(s_name)
                # 第二步：别名不匹配，再判断名称匹配
                if not match:
                    match = (s_name == select_data)
                # 第三步：名称也不匹配，最后尝试ID匹配
                if not match:
                    try:
                        match = (s_id == int(select_data))
                    except (ValueError, TypeError):
                        # ID匹配失败，保持match=False即可，无需额外操作
                        pass

            # 匹配成功：构造简化结构并添加
            if match:
                full_aliases = name_to_full_aliases_map.get(s_name, s_aliases)
                # 单字典整合song对象和别名，简化返回结构
                result_item = {
                    "song": song,
                    "aliases": full_aliases
                }
                result_list.append(result_item)

    return result_list


# ---------------- 以下为指令层封装 ----------------

# 检索类型常量
BY_ID = 0
BY_NAME = 1
BY_ALIAS = 2
BY_ANY = 3

NOT_FOUND = "Liz没有找到这样的歌"


def query_by_id(keyword: str):
    """按曲目 ID 检索。"""
    return select_song(select_data=keyword, select_type=BY_ID)


def query_by_name(keyword: str):
    """按歌名精确检索。"""
    return select_song(select_data=keyword, select_type=BY_NAME)


def query_by_alias(keyword: str):
    """按别名检索。"""
    return select_song(select_data=keyword, select_type=BY_ALIAS)


def query_any(keyword: str):
    """混合检索：别名 → 歌名 → ID。"""
    return select_song(select_data=keyword, select_type=BY_ANY)


def format_song(song_data: dict) -> str:
    """把单首歌曲对象格式化为回复文本。

    re:master 难度可能不存在（谱面数组不足 5 项），此时降级显示 "-"。
    """
    try:
        rem = song_data['charts'][4]
        return (f"\n乐曲名称：{song_data['name']} \n曲师：{song_data['artist']} "
                f"\nid：{song_data['id']} \nmaster难度：{song_data['charts'][3]['level']} "
                f"\nma谱师：{song_data['charts'][3]['charter']} "
                f"\nre:master难度：{rem['level']} \nrem谱师：{rem['charter']}")
    except IndexError:
        return (f"\n乐曲名称：{song_data['name']} \n曲师：{song_data['artist']} "
                f"\nid：{song_data['id']} \nmaster难度：{song_data['charts'][3]['level']} "
                f"\nma谱师：{song_data['charts'][3]['charter']} "
                f"\nre:master难度： - \nrem谱师： -")


def song_reply(keyword: str, select_type: int) -> str:
    """查歌指令的统一回复入口。

    :param keyword: 检索关键词
    :param select_type: 0/1/2/3 见 select_song
    :return: 格式化后的回复文本，未命中返回 NOT_FOUND
    """
    try:
        song_data = select_song(select_data=keyword, select_type=select_type)
    except IndexError:
        return NOT_FOUND
    if not song_data:
        return NOT_FOUND
    return format_song(song_data[0]['song'])


def songdata_reply(keyword: str) -> str:
    """原样输出检索到的完整结构（调试用）。"""
    try:
        return str(select_song(select_data=keyword, select_type=BY_ID))
    except IndexError:
        return "DataError"


def alias_reply(keyword: str) -> str:
    """查询某首歌的全部别名。"""
    try:
        song_data = select_song(select_data=keyword, select_type=BY_ANY)
        if not song_data:
            return NOT_FOUND
        target_name = song_data[0]['song']['name']
        with open(ALIAS_JSON, 'rb') as f_alias:
            for song in ijson.items(f_alias, 'item'):
                if song.get("name") == target_name:
                    return f"歌曲有以下别名：{song.get('alias')}"
        # 在 alias.json 中没有对应条目
        return NOT_FOUND
    except (IndexError, TypeError):
        return "笨蛋是不是没写参数啊"
