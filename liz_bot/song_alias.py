"""歌曲别名管理 —— 向 alias.json 添加别名。

主要对外接口：
    add_song_alias(song_name, new_alias, alias_file_path)
    add_alias_reply(keyword, new_alias)   指令层封装，直接产出回复文本
"""

import json
import os

from liz_bot.song_paths import ALIAS_JSON
from liz_bot.song_query import NOT_FOUND, query_any

# 添加失败时的统一提示
ADD_FAILED = "添加失败，找不到歌曲或别名已存在！"
ADD_BAD_PARAMS = "笨蛋传错参数了呢..."


def add_song_alias(song_name: str, new_alias: str, alias_file_path=ALIAS_JSON):
    """
    给alias.json文件添加字符串别名到原有alias列表中（不改变列表结构，自动去重）
    第一版路径参数：手动传入alias.json完整路径，兼容原有JSON结构
    :param alias_file_path: alias.json文件的完整路径（如 "maimaiDX_songs/alias.json"）
    :param song_name: 歌曲名（需与JSON中的name格式一致，如 "\"411Ψ892\""）
    :param new_alias: 要添加的单个别名（字符串类型，如 "新别名114"）
    :return: 布尔值，True表示添加成功，False表示失败
    """
    # 步骤1：参数校验（确保新别名是非空字符串，避免无效数据）
    if not isinstance(new_alias, str) or len(new_alias.strip()) == 0:
        print("错误：新别名必须是非空字符串！")
        return False
    new_alias = new_alias.strip()  # 去除首尾空白，避免无效空格存入列表

    # 步骤2：读取现有alias.json数据（兼容文件不存在/格式校验）
    song_alias_list = []
    if os.path.exists(alias_file_path):
        try:
            with open(alias_file_path, 'r', encoding='utf-8') as f:
                song_alias_list = json.load(f)
            # 校验数据格式：必须是列表（兼容原有JSON结构）
            if not isinstance(song_alias_list, list):
                print("错误：alias.json文件格式无效，必须是JSON列表！")
                return False
        except Exception as e:
            print(f"错误：读取alias.json失败 - {e}")
            return False

    # 步骤3：判断歌曲是否存在，将字符串别名添加到alias列表（不改变列表结构）
    song_exists = False
    for item in song_alias_list:
        # 严格匹配歌曲名，避免误修改
        if item.get("name") == song_name:
            song_exists = True
            # 确保alias字段是列表（兼容原有结构，防止异常）
            existing_alias_list = item.get("alias", [])
            if not isinstance(existing_alias_list, list):
                existing_alias_list = []  # 若意外不是列表，强制转为列表，保证结构一致

            # 去重：仅当列表中不存在该字符串别名时，才添加（避免冗余）
            if new_alias not in existing_alias_list:
                existing_alias_list.append(new_alias)  # 字符串别名入列表，不改变列表结构
            # 更新原有alias列表
            item["alias"] = existing_alias_list
            break  # 找到对应歌曲，退出循环

    # 步骤4：若歌曲不存在，新增条目（alias仍为列表，仅包含该字符串别名）
    if not song_exists:
        new_song_item = {
            "name": song_name,
            "alias": [new_alias]  # 保持alias为列表结构，存入单个字符串别名
        }
        song_alias_list.append(new_song_item)

    # 步骤5：写入文件（保留原有格式，不破坏结构）
    try:
        with open(alias_file_path, 'w', encoding='utf-8') as f:
            json.dump(song_alias_list, f, ensure_ascii=False, indent=2)
        print(f"成功！字符串别名「{new_alias}」已添加到歌曲「{song_name}」的alias列表中")
        return True
    except Exception as e:
        print(f"错误：写入alias.json失败 - {e}")
        return False


# ---------------- 以下为指令层封装 ----------------

def add_alias_reply(keyword: str, new_alias: str) -> str:
    """添加别名指令的回复入口。

    :param keyword: 用于定位歌曲的关键词（混合检索）
    :param new_alias: 要添加的别名
    :return: 回复文本
    """
    try:
        matched = query_any(keyword)
        if not matched:
            return ADD_FAILED
        song_name = matched[0]['song']['name']
        if not song_name or not add_song_alias(song_name, new_alias):
            return ADD_FAILED
        return f"别名{new_alias}添加已添加到歌曲{song_name}"
    except (IndexError, TypeError):
        return ADD_BAD_PARAMS
