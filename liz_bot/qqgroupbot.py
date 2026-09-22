import os
import time
import random
import asyncio
from typing import Dict
import re
import botpy
from botpy import logging
from botpy.message import GroupMessage
from botpy import logging
from botpy.ext.command_util import Commands
from botpy.message import GroupMessage, Message
from liz_bot.command_handler import handle_command, parse_command
from liz_bot.config import BotConfig, load_bot_config

_log = logging.get_logger()

# 日志文件统一存放目录（项目根目录下的 bot_log/）
# botpy 默认把日志写到 os.getcwd()，此处显式指定为 bot_log 文件夹
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot_log")
os.makedirs(LOG_DIR, exist_ok=True)

# 追加文件 handler：格式与 botpy 默认完全一致，仅改变存放路径
LOG_FILE_HANDLER = {
    "handler": logging.DEFAULT_FILE_HANDLER["handler"],
    "format": logging.DEFAULT_FILE_HANDLER["format"],
    "level": logging.DEFAULT_FILE_HANDLER["level"],
    "when": logging.DEFAULT_FILE_HANDLER["when"],
    "backupCount": logging.DEFAULT_FILE_HANDLER["backupCount"],
    "encoding": logging.DEFAULT_FILE_HANDLER["encoding"],
    "filename": os.path.join(LOG_DIR, "%(name)s.log"),
}

# 优化：带过期时间的缓存（替代原MSG_DUP_CACHE）
class ExpiringCache:
    def __init__(self, expire_seconds: int = 3):
        self.cache: Dict[str, float] = {}
        self.expire = expire_seconds
        # 启动后台清理任务（守护任务，不阻塞退出）
        asyncio.create_task(self._clean_loop(), name="cache_cleaner")

    def add(self, key: str):
        """添加缓存键，值为当前时间戳"""
        self.cache[key] = time.time()

    def exists(self, key: str) -> bool:
        """检查键是否存在且未过期"""
        if key not in self.cache:
            return False
        # 过期则自动删除并返回False
        if time.time() - self.cache[key] > self.expire:
            del self.cache[key]
            return False
        return True

    async def _clean_loop(self):
        """后台循环清理过期缓存（每10秒执行一次）"""
        while True:
            await asyncio.sleep(10)
            current_time = time.time()
            # 批量删除过期键
            expired_keys = [k for k, v in self.cache.items() if current_time - v > self.expire]
            for k in expired_keys:
                del self.cache[k]
        # 注：守护任务会随主事件循环退出而终止，无内存泄漏



# 机器人核心类
class MyClient(botpy.Client):
    none_reply = ['干什么！', 'Liz在哦', '宝宝在干嘛？', 'Liz随时待命', 'suki❤', 'Liz is on ready!']

    async def on_ready(self):
        _log.info(f"机器人 {self.robot.name} 已就绪！")

    async def on_group_at_message_create(self, message: GroupMessage):
        """监听群聊@消息，解析并处理指令"""
        if message.content.strip() == "":
            await message.reply(content=random.choice(self.none_reply))
        else:
            try:
                # 1. 解析指令
                cmd_name, cmd_params, is_valid = await parse_command(message.content)

                # 2. 处理解析结果
                if not is_valid:
                    # 非/开头的消息，返回"未知的指令"
                    await message.reply(content=f"未知指令：“{message.content}”")
                else:
                    # 解析成功，分发到指令处理器
                    a = await handle_command(cmd_name, cmd_params)
                    await message.reply(content=a)

            except Exception as e:
                _log.error(f"处理消息失败：{e}")
                await self.api.post_group_message(
                    group_openid=message.group_openid,
                    msg_type=0,
                    content=f"处理失败：{str(e)[:20]}..."
                )


# 启动机器人
def run_bot(config: BotConfig) -> None:
    """启动机器人。

    :param config: 机器人凭据，由调用方显式传入（见 ``liz_bot/config.py``）。
        不再从模块级全局变量读取，便于测试与在云平台上改用环境变量。
    """
    intents = botpy.Intents(public_messages=True)
    # 不启用 botpy 默认 handler（避免在 cwd 再生成一份 botpy.log），改用指向 bot_log/ 的 handler
    client = MyClient(intents=intents, ext_handlers=LOG_FILE_HANDLER)
    client.run(appid=config.appid, secret=config.secret)

if __name__ == "__main__":
    run_bot(load_bot_config())