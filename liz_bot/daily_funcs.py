"""日常附加功能 —— 与曲库无关的杂项指令。

目前包含：
    random_int_from_list   随机数（骰子）
    greeting_reply         问候语
    help_reply             帮助

注：原有的 ``qr_reply``（扫码查询）已**暂时移除**——它是对舞萌服务端的
发包功能（POST 到 ``ai.sys-allnet.cn``）。恢复步骤见
``liz_bot/_已移除功能_舞萌发包.md``。
"""

import random

HELP_TEXT = "我不知道，问desive哦"


def random_int_from_list(param_list: list) -> str:
    """
    随机数生成规则：
    1. 无参数 → 返回1-6随机数（字符串）
    2. 参数列表为[int1, int2]（两个纯数字）→ 返回int1到int2的随机数（字符串）
    3. 其他参数情况 → 从参数列表中随机选一个元素返回（字符串）
    """
    # 无参数：返回1-6随机数
    if not param_list:
        return str(random.randint(1, 6))

    # 检查是否为[int1, int2]格式（两个元素且均为数字）
    if len(param_list) == 2:
        try:
            # 尝试将两个参数转为整数（仅校验数字，不做过多类型检查）
            int1 = int(param_list[0])
            int2 = int(param_list[1])
            # 确保区间合法性（小值在前）
            start, end = (int1, int2) if int1 <= int2 else (int2, int1)
            # 返回区间随机数（转字符串）
            return str(random.randint(start, end))
        except (ValueError, TypeError):
            # 非数字参数 → 走“随机选元素”逻辑
            pass

    # 其他情况：从参数列表中随机选一个元素返回（转字符串）
    random_param = random.choice(param_list)
    return str(random_param)


def greeting_reply() -> str:
    """问候语。"""
    return "你好呀！我是Liz～"


def help_reply() -> str:
    """帮助文本。"""
    return HELP_TEXT
