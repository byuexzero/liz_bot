"""歌曲别名管理 —— **已暂时移除实现，仅保留接口**。

为什么是空实现
--------------
别名功能原本把用户新增的别名写进 ``liz_bot/maimaiDX_songs/alias.json``。
查歌切换到水鱼（diving-fish）曲库后，该曲库**不含任何别名数据**，
别名检索已无从建立，因此按重构计划「暂时删除、不做重构」。

本模块保留同名接口与同名常量，使 ``command_router`` / ``command_handler``
的调用点无需改动（即「返回空数据以适配」）。所有函数都不再读写磁盘。

注意 ``add_alias_reply`` 返回的是 ``alias.add_failed`` 的文案而**不是空字符串**：
``qqgroupbot`` 会把回复原样交给 ``message.reply(content=...)``，
空字符串会被 QQ 接口拒绝，反而变成报错。

文案位置
--------
本模块的提示文案**不再硬编码**，来自 ``liz_bot/texts/replies.json``
（键 ``alias.add_failed`` / ``alias.add_bad_params``，见 :mod:`liz_bot.replies`）。

恢复步骤
--------
原始实现（103 行，含 alias.json 的读写与去重）与数据文件
``alias.json``（361538 字节）都已归档到::

    _backup/liz_bot_song_db_2026-09-23/

详见该目录的 ``README.md``。恢复时需要一并恢复 ``runtime_paths.ALIAS_JSON``
与 ``ensure_dirs()`` 里的空卷播种逻辑。
"""

from liz_bot import replies

#: 历史常量名 → 回复文本键。文案在 ``liz_bot/texts/replies.json``。
#:
#: 用模块级 ``__getattr__``（PEP 562）保留 ``ADD_FAILED`` / ``ADD_BAD_PARAMS``
#: 这两个既有常量名（``command_router`` 在用 ``ADD_BAD_PARAMS``，
#: 部署自检脚本会读 ``ENABLED``），同时让改文件后取值立刻生效。
_LEGACY_ALIASES = {
    "ADD_FAILED": "alias.add_failed",
    "ADD_BAD_PARAMS": "alias.add_bad_params",
}


def __getattr__(name: str) -> str:
    key = _LEGACY_ALIASES.get(name)
    if key is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return replies.text(key)


#: 别名功能当前是否可用。调用方可用它决定是否展示相关提示。
ENABLED = False


def add_song_alias(song_name: str, new_alias: str, alias_file_path=None) -> bool:
    """**已暂时移除实现** —— 不做任何写入，恒返回 ``False``。

    签名与原实现保持一致，使既有调用点（含部署自检脚本）无需改动。

    :return: 恒为 ``False``（表示未添加成功）
    """
    return False


# ---------------- 以下为指令层封装 ----------------

def add_alias_reply(keyword: str, new_alias: str) -> str:
    """添加别名指令的回复入口。

    **已暂时移除实现**，恒返回 ``alias.add_failed`` 的文案。

    :return: ``replies.json`` 中 ``alias.add_failed`` 的值（保证非空）
    """
    return replies.text("alias.add_failed")
