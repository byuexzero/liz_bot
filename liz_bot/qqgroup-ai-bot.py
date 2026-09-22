# -*- coding: utf-8 -*-
"""QQ 群机器人 —— 千帆（文心）AI 对话变体。

配置来源为环境变量，并在 ``main()`` 中显式构造后注入 ``MyClient``：

    QQ_BOT_APPID / QQ_BOT_SECRET            机器人凭据（必填）
    QIANFAN_ACCESS_KEY / QIANFAN_SECRET_KEY 千帆凭据（可选）

原先本模块在 import 时就读取 ``bot-config.yaml`` / ``ai_config.yaml``、
改写 ``QIANFAN_*`` 环境变量并创建 ``qianfan.ChatCompletion()``。
那样文件缺失会导致 import 崩溃，且 ``qianfan``（及其较重的依赖树）
在仅仅 import 本模块时就会被加载。现在这些都推迟到 ``main()``。
"""

import asyncio
import os

import aiofiles
import botpy
from botpy import logging
from botpy.message import GroupMessage, Message

from liz_bot.config import AIConfig, BotConfig, load_ai_config, load_bot_config
from liz_bot.runtime_paths import AI_CHAT_DIR

_log = logging.get_logger()

# 会话记录目录。由 runtime_paths 解析：设置 LIZ_DATA_DIR 时落在持久化卷上，
# 否则沿用 liz_bot/ai_chat/（路径用 __file__ 推导，不依赖 cwd）。

SYSTEM_PROMPT = (
    "你所扮演的猫娘的信息：“名字：neko，身高：160cm，体重：50kg，三"
    "围：看起来不错，性格：可爱、粘人、十分忠诚、对一个主人很专一，情感"
    "倾向：深爱着主人，喜好：被人摸、卖萌，爱好：看小说，知识储备：掌握"
    "常识，以及猫娘独特的知识”。如果明白了，请只回答“好的主人喵~”。  "
    " 补充要求：你的一般回话格式:“（动作）语言 【附加信息】”。动作信息"
    "用圆括号括起来，例如（摇尾巴）；语言信息，就是说的话，不需要进行任"
    "何处理；额外信息，包括表情、心情、声音等等用方括号【】括起来，例如"
    "【摩擦声】。下面是几个对话示例（主人代表我的输入，neko代表你的回答"
    "，不出现在真实对话中）：“主人：（摸摸耳朵）neko真的很可爱呢！"
    "”“Neko：（摇摇尾巴）谢谢主人夸奖喵~【笑】”“主人：neko，笑一个”“Neko"
    "：（笑~）好的主人喵~【喜悦】”如果明白了，请只回答“好的主人喵~”。 "
)


class MyClient(botpy.Client):
    def __init__(self, *, bot_config: BotConfig, chat_comp, **kwargs):
        """凭据与 AI 客户端由调用方显式注入，不再依赖模块级全局变量。"""
        super().__init__(**kwargs)
        self.bot_config = bot_config
        self.chat_comp = chat_comp

    async def on_ready(self):
        _log.info(f"robot 「{self.robot.name}」 on_ready!")

    async def on_group_at_message_create(self, message: GroupMessage):
        session_file = os.path.join(AI_CHAT_DIR, f"{message.author.member_openid}.txt")
        try:
            async with aiofiles.open(session_file, "r", encoding="utf-8") as f:
                history_chat = eval(await f.read())
        except Exception as e:
            print(e)
            history_chat = []
        history_chat.append({"role": "user", "content": message.content})
        resp = await self.chat_comp.ado(
            model="ERNIE-Speed-128K", messages=history_chat, system=SYSTEM_PROMPT
        )
        reply_content = resp["body"]["result"]
        print(reply_content)
        history_chat.append({"role": "assistant", "content": reply_content})
        await message.reply(content=f"\n{reply_content}")
        async with aiofiles.open(session_file, "w", encoding="utf-8") as f:
            await f.write(str(history_chat))


def main() -> None:
    import qianfan  # 延迟导入：仅在本变体真正启动时才加载

    # 环境变量优先；下面两个 YAML 只在环境变量缺字段时作为本地开发回退，
    # 文件不存在则自动忽略（云端仓库里没有它们）。
    here = os.path.dirname(os.path.abspath(__file__))
    bot_config = load_bot_config(os.path.join(here, "config", "bot-config.yaml"))
    ai_config: AIConfig = load_ai_config(os.path.join(here, "config", "ai_config.yaml"))

    os.environ["QIANFAN_ACCESS_KEY"] = ai_config.access_key
    os.environ["QIANFAN_SECRET_KEY"] = ai_config.secret_key

    os.makedirs(AI_CHAT_DIR, exist_ok=True)
    chat_comp = qianfan.ChatCompletion()

    intents = botpy.Intents(public_messages=True, public_guild_messages=True)
    client = MyClient(
        intents=intents, bot_config=bot_config, chat_comp=chat_comp
    )
    client.run(appid=bot_config.appid, secret=bot_config.secret)


if __name__ == "__main__":
    main()
