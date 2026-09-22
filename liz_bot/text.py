# -*- coding: utf-8 -*-
import asyncio
import os

import botpy
from botpy import logging
from botpy.ext.cog_yaml import read
from botpy.ext.command_util import Commands
from botpy.message import GroupMessage, Message

test_config = read(os.path.join(os.path.dirname(__file__), "./config/config.yaml"))

_log = logging.get_logger()


@Commands('图片测试', )
async def pic(api: botpy.BotAPI, message: GroupMessage, params=None):
    pic_url1 = 'https://img.zcool.cn/community/01bb815b4c8b5ca80121ade02bfb4f.jpg'
    pic_url2 = 'https://img.alicdn.com/i1/374544688/O1CN01bx03zk1kV9LDwHjzf_!!374544688.jpg'
    upload_media1 = await api.post_group_file(
        group_openid=message.group_openid,
        file_type=1,  # 文件类型要对应上，具体支持的类型见方法说明
        url=pic_url1  # 文件Url
    )
    upload_media2 = await api.post_group_file(
        group_openid=message.group_openid,
        file_type=1,  # 文件类型要对应上，具体支持的类型见方法说明
        url=pic_url2  # 文件Url
    )
    await message.reply(
        msg_type=7,  # 7表示富媒体类型
        media=upload_media1,
        msg_seq=2
    )
    await api.post_group_message(
        group_openid=message.group_openid,
        msg_type=7,  # 7表示富媒体类型
        msg_id=message.id,
        media=upload_media2,
        msg_seq=3
    )
    return True


class MyClient(botpy.Client):
    async def on_ready(self):
        _log.info(f"robot 「{self.robot.name}」 on_ready!")

    async def on_group_at_message_create(self, message: GroupMessage):
        await message.reply(content=f"bot收到了消息：“{message.content}”")
        handlers = [pic]
        for handler in handlers:
            if await handler(api=self.api, message=message):
                return 0


if __name__ == "__main__":
    # 通过预设置的类型，设置需要监听的事件通道
    # intents = botpy.Intents.none()
    # intents.public_messages=True

    # 通过kwargs，设置需要监听的事件通道
    intents = botpy.Intents(public_messages=True)
    client = MyClient(intents=intents)
    client.run(appid=test_config["appid"], secret=test_config["secret"])