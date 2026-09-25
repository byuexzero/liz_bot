import asyncio
import os
import random
import time
from typing import Dict

import botpy
from botpy import logging
from botpy.message import GroupMessage

from liz_bot import media_upload, replies
from liz_bot.command_router import RichReply, reply_text
from liz_bot.config import BotConfig, load_bot_config
from liz_bot.healthz import set_status as set_health_status
from liz_bot.runtime_paths import LOG_DIR

_log = logging.get_logger()

# 日志文件统一存放目录。
# botpy 默认把日志写到 os.getcwd()，此处显式指定，避免"从哪个目录启动"影响落盘位置。
# 路径由 runtime_paths 解析：设置了 LIZ_DATA_DIR（容器平台挂载持久化卷）时日志
# 落在卷上，否则沿用项目根目录下的 bot_log/。
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
        #
        # ⚠️ 必须**持有任务引用**：``asyncio`` 只对 task 持弱引用，不保存的话
        # 它可能在执行到 ``await`` 之前就被 GC 掉（官方文档明确警告
        # "Save a reference to the result of this function"）。
        # 这里存到实例属性上，随实例一起存活。
        self._clean_task = asyncio.create_task(self._clean_loop(), name="cache_cleaner")

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


#: 消息去重窗口（秒）。
#:
#: QQ 开放平台在超时重推时会**把同一条消息投递多次**，不去重就会回复多次。
#: 3 秒足够覆盖平台的重试间隔，又短到不会把「用户真的连发两条」误判成重复
#: （那种情况 message id 不同，本来也不会命中）。
DUP_WINDOW_SECONDS = 3

#: 消息去重缓存。**惰性创建**，原因见 :func:`_get_dup_cache`。
_dup_cache: "ExpiringCache | None" = None


def _get_dup_cache() -> ExpiringCache:
    """取（必要时创建）消息去重缓存。

    ⚠️ 不能写成模块级 ``_dup_cache = ExpiringCache()``：``ExpiringCache.__init__``
    会 ``asyncio.create_task``，而模块 import 时**没有运行中的事件循环**，
    会直接抛 ``RuntimeError: no running event loop``。
    本函数只在协程里被调用（消息回调），那里一定有循环。

    惰性创建而非放在 ``on_ready`` 里，是为了**不依赖回调顺序** ——
    万一在 ``on_ready`` 之前就来消息，去重也不会失效。
    """
    global _dup_cache
    if _dup_cache is None:
        _dup_cache = ExpiringCache(DUP_WINDOW_SECONDS)
        _log.info("消息去重已启用（窗口 %ds）", DUP_WINDOW_SECONDS)
    return _dup_cache



#: 错误提示用的 ``msg_seq``。
#:
#: ``message.reply()`` 内部就是 ``post_group_message(msg_id=..., msg_seq=1)``，
#: 而「相同的 msg_id + msg_seq 重复发送会失败」（见 botpy ``api.py``），
#: 所以出错时要用**另一个** seq 才能发得出去。
_ERROR_MSG_SEQ = 2

#: 发图失败、退回文字版时用的 ``msg_seq``。
#:
#: 用 3 而不是 1 或 2：``seq=1`` 可能已被那次发图占用（发图本身走
#: ``msg_id + msg_seq=1``），``seq=2`` 是 :data:`_ERROR_MSG_SEQ` 的地盘。
#: 三个 seq 互不相同，任何一条路径都能发得出去。
_RICH_FALLBACK_MSG_SEQ = 3


def _session_key(message: GroupMessage) -> str:
    """多轮补参的会话键：``群 openid:成员 openid``。

    **必须带群** —— ``member_openid`` 只在同一个群内稳定，只用它会把不同群里的
    同一个人串成一条会话（A 群没收完的补参会跑到 B 群去续）。

    任一部分缺失时返回空串 —— 此时不启用补参。宁可没有这个功能，
    也不要因为一个缺失字段把所有人的补参状态混到一起。
    """
    group = message.group_openid
    member = getattr(message.author, "member_openid", None)
    if not group or not member:
        return ""
    return f"{group}:{member}"


# 机器人核心类
#
# 注：本类的全部对外文案（空消息随机回复、非指令提示、异常提示）都来自
# ``liz_bot/texts/replies.json``，见 :mod:`liz_bot.replies`。原先的
# ``none_reply`` 类属性已去掉 —— 类属性在 import 时求值并冻结，
# 改文案不会生效；现在改为每次使用时取值。
class MyClient(botpy.Client):

    async def on_ready(self):
        _log.info(f"机器人 {self.robot.name} 已就绪！")
        # 让健康检查端点反映真实就绪状态（见 liz_bot/healthz.py）
        set_health_status("ready")

    async def on_group_at_message_create(self, message: GroupMessage):
        """监听群聊@消息，解析并处理指令"""
        # 0. 消息去重 —— QQ 开放平台超时重推时会**把同一条消息投递多次**，
        #    不去重就会重复回复（每条回复都占主动消息配额，还可能触发限频）。
        #    判据优先用 ``message.id``（平台侧消息唯一标识），
        #    它缺失时退回 ``event_id``；两者都没有就跳过去重（不阻断正常流程）。
        #
        #    放在**最前面**：重复的空消息也不该被回两次随机文案；
        #    而且补参状态下重复投递会把同一个参数叠两次，所以去重必须在它之前。
        dup_key = message.id or message.event_id
        if dup_key:
            cache = _get_dup_cache()
            if cache.exists(dup_key):
                _log.info("忽略重复投递的消息：id=%s", dup_key)
                return
            cache.add(dup_key)

        if message.content.strip() == "":
            # 空消息时的随机回复候选，文案见 replies.json 的 bot.none_reply。
            # 刻意**不动**补参状态 —— 空消息通常只是误触，不该把用户正在补的
            # 参数丢掉（超时自会作废，见 liz_bot/pending.py）。
            await message.reply(content=random.choice(replies.get("bot.none_reply")))
        else:
            try:
                # 前缀识别（`/` 本机指令 / `#` 舞萌命名空间）、解析、分发，
                # 全部在 command_router.reply_text 里 —— 本类只负责收发、
                # 补参会话键，以及「空消息」「发图」「异常」这三个外壳行为。
                #
                # ``rich=True`` 允许把结果渲染成图片：判定细节是 5 列表格，
                # 靠空格对齐在 QQ 的比例字体下**必然错位**，出图才看得清。
                reply = await reply_text(
                    message.content, session_key=_session_key(message), rich=True)

                if isinstance(reply, RichReply):
                    await self._send_rich(message, reply)
                else:
                    await message.reply(content=reply)

            except Exception as e:
                _log.error(f"处理消息失败：{e}")
                await self._reply_error(message, e)

    async def _reply_error(self, message: GroupMessage, error: Exception) -> None:
        """把异常回给用户。

        ⚠️ **必须带上 ``msg_id``** —— 不带的话这就成了「主动消息」，
        要消耗主动消息配额（每条群每月额度有限，见 QQ 开放平台文档）。
        正常回复走 ``message.reply()`` 是**被动**回复、不花配额，
        出错时反而去花配额是反的。

        ``msg_seq`` 用 2 而不是 1：``seq=1`` 已被 ``message.reply()`` 占用，
        且「相同的 msg_id + msg_seq 重复发送会失败」。
        """
        await self.api.post_group_message(
            group_openid=message.group_openid,
            msg_id=message.id,
            msg_seq=_ERROR_MSG_SEQ,
            msg_type=0,
            content=replies.text("bot.error", error=str(error)[:20]),
        )

    async def _send_rich(self, message: GroupMessage, reply: RichReply) -> None:
        """把富媒体（图片）发到群里；**任何失败都退回文字版**。

        图片是锦上添花：上传要经过 4 次网络往返（prepare → PUT 分片 →
        part_finish → 合并），中间任何一步抖动都不该让用户什么都收不到。
        而 ``reply.fallback`` 本来就是同一份文字版回复，退回去零成本。

        本方法**刻意不抛异常** —— 发图失败已经被处理掉了，再往外抛只会让
        外层把它当成「处理消息失败」并再回一句错误提示，等于同一件事报两次。
        """
        try:
            await media_upload.send_group_image(
                self.api,
                message.group_openid,
                reply.png,
                msg_id=message.id,
                msg_seq=1,  # 被动回复，不消耗主动消息配额
                filename=reply.filename,
            )
            return
        except Exception:
            _log.exception("发图失败，退回文字版")

        try:
            await self.api.post_group_message(
                group_openid=message.group_openid,
                msg_id=message.id,
                msg_seq=_RICH_FALLBACK_MSG_SEQ,
                msg_type=0,
                content=reply.fallback,
            )
        except Exception:
            # 连降级都失败就只能记日志了 —— 用户收不到东西，但至少能查到原因。
            _log.exception("发图失败后的文字版降级也失败了")


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