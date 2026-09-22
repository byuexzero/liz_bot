# -*- coding: utf-8 -*-
import asyncio
import os

import aiofiles
import aiohttp
import qianfan
import botpy
from botpy import logging
from botpy.ext.cog_yaml import read
from botpy.message import GroupMessage, Message

test_config = read(os.path.join(os.path.dirname(__file__), "./config/bot-config.yaml"))
ai_config = read(os.path.join(os.path.dirname(__file__), "./config/ai_config.yaml"))
_log = logging.get_logger()

os.environ["QIANFAN_ACCESS_KEY"] = ai_config['ACCESS_KEY']
os.environ["QIANFAN_SECRET_KEY"] = ai_config['SECRET_KEY']
chat_comp = qianfan.ChatCompletion()


class MyClient(botpy.Client):
    async def on_ready(self):
        _log.info(f"robot 「{self.robot.name}」 on_ready!")

    async def on_group_at_message_create(self, message: GroupMessage):
        # print(message.content)
        # resp = await chat_comp.ado(model = "ERNIE-4.0-8K", messages = [{"role": "user", "content": message.content}])
        # print(resp['body']['result'])
        # await message.reply(content = resp['body']['result'])

        try:
            async with aiofiles.open(f"./ai_chat/{message.author.member_openid}.txt", 'r', encoding = 'utf-8') as f:
                history_chat = eval(await f.read())
        except Exception as e:
            print(e)
            history_chat = []
        history_chat.append({"role": "user", "content": message.content})
        resp = await chat_comp.ado(model = "ERNIE-Speed-128K", messages = history_chat, system="你所扮演的猫娘的信息：“名字：neko，身高：160cm，体重：50kg，三"
                                                                                                 "围：看起来不错，性格：可爱、粘人、十分忠诚、对一个主人很专一，情感"
                                                                                                 "倾向：深爱着主人，喜好：被人摸、卖萌，爱好：看小说，知识储备：掌握"
                                                                                                 "常识，以及猫娘独特的知识”。如果明白了，请只回答“好的主人喵~”。  "
                                                                                                 " 补充要求：你的一般回话格式:“（动作）语言 【附加信息】”。动作信息"
                                                                                                 "用圆括号括起来，例如（摇尾巴）；语言信息，就是说的话，不需要进行任"
                                                                                                 "何处理；额外信息，包括表情、心情、声音等等用方括号【】括起来，例如"
                                                                                                 "【摩擦声】。下面是几个对话示例（主人代表我的输入，neko代表你的回答"
                                                                                                 "，不出现在真实对话中）：“主人：（摸摸耳朵）neko真的很可爱呢！"
                                                                                                 "”“Neko：（摇摇尾巴）谢谢主人夸奖喵~【笑】”“主人：neko，笑一个”“Neko"
                                                                                                 "：（笑~）好的主人喵~【喜悦】”如果明白了，请只回答“好的主人喵~”。 ")
        reply_content = resp["body"]['result']
        print(reply_content)
        history_chat.append({"role": "assistant", "content": reply_content})
        await message.reply(content = f"\n{reply_content}")
        async with aiofiles.open(f"./ai_chat/{message.author.member_openid}.txt", 'w', encoding = 'utf-8') as f:
            await f.write(str(history_chat))


if __name__ == "__main__":
    intents = botpy.Intents(public_messages = True,public_guild_messages = True)
    client = MyClient(intents = intents)
    client.run(appid = test_config["appid"], secret = test_config["secret"])
