import asyncio
import os
import random
import time
from typing import Dict

import botpy
from botpy import logging
from botpy.message import GroupMessage

from liz_bot import ai_chat, media_upload, proactive, replies
# 注：``PREFIX_NORMAL`` / ``PREFIX_MAIMAI`` 现在只在 ``ai_chat.should_handle``
# 里用到（触发判断收在那边，便于自检），本模块不再直接引。
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
#:
#: ⚠️ 2 与 :data:`_BUBBLE_MSG_SEQ_BASE` 起的 AI 气泡**共用编号** —— 安全，
#: 因为同一条 ``msg_id`` 要么走 AI 分支、要么走指令分支，不会两者都走
#: （AI 分支处理完直接 ``return``）。真正的约束只有一条：
#: **同一个 msg_id 下不许重复用同一个 seq**。
_ERROR_MSG_SEQ = 2

#: 发图失败、退回文字版时用的 ``msg_seq``。
#:
#: 用 3 而不是 1 或 2：``seq=1`` 可能已被那次发图占用（发图本身走
#: ``msg_id + msg_seq=1``），``seq=2`` 是 :data:`_ERROR_MSG_SEQ` 的地盘。
#: 五个 seq 互不相同，任何一条路径都能发得出去。（与 AI 气泡共用编号的理由同上。）
_RICH_FALLBACK_MSG_SEQ = 3

#: 「任务进行中先发一条」用的 ``msg_seq``（见 :meth:`MyClient._notify`）。
#:
#: 目前只有 ``#上传`` 用：它真跑约 80 秒，开始前先发一句预计等待时长。
#: 用 4 而不是 1 —— 那条预告发完之后，**结果**还要用 ``seq=1`` 发出去，
#: 两者不能撞。同样走被动回复（带 ``msg_id``），**不消耗主动消息配额**。
_NOTIFY_MSG_SEQ = 4

#: **合规兜底**用的 ``msg_seq``（见 :meth:`MyClient._send_safe`）。
#:
#: 回复被平台拒发时，用 5 补一条固定推托。⚠️ 群聊被动回复的 ``msg_seq``
#: 上限就是 5（``1``~``5``），这里正好是最后一个空位 —— **再加路径就得复用**。
_SAFE_FALLBACK_MSG_SEQ = 5

#: AI 回复的**气泡**从哪个 ``msg_seq`` 开始（见 :meth:`MyClient._send_bubbles`）。
#:
#: ⚠️⚠️ 坑位只有 5 个，来源是官方文档《消息收发概述》的「频率与时效规则」：
#: **群聊被动消息 —— 有效期 5 分钟，每条消息可回复 5 次**。分配表：
#:
#: ====  ==========================================================
#: seq   用途
#: ====  ==========================================================
#: 1~3   AI 回复的气泡（最多 ``ai_chat.MAX_BUBBLES`` 条）
#: 4     ``#上传`` 的「预计等待」预告
#: 5     合规兜底（被平台拒发时补的固定推托）
#: ====  ==========================================================
#:
#: ⇒ ``ai_chat.MAX_BUBBLES`` **不能超过 3**，否则会顶掉兜底那一格。
_BUBBLE_MSG_SEQ_BASE = 1

#: 气泡之间的间隔（秒）。
#:
#: 真人不会在 50 毫秒内连发三条 —— 不加间隔的话，三条气泡几乎同时到达，
#: 反而比一条整段更机械（2026-09-29 用户要求「减少生硬感」）。
_BUBBLE_GAP_SECONDS = 0.4


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
        # 群聊**主动推送**（2026-09-29）。默认关闭 ——
        # 两个环境变量任一为空就什么都不做，见 liz_bot/proactive.py 的模块注释。
        # ⚠️ 必须防重复启动：gateway 心跳重连会再次触发 on_ready，
        #    不去重的话每重连一次就多一个定时器（去重逻辑在 proactive.start 里）。
        proactive.start(self.push_proactive)

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

        # 附件诊断：**只记数量与类型，绝不记正文**（群里的话不该进日志）。
        # ⚠️ 为什么值得专门记：QQ 群聊的图片消息到底以什么形态送达
        #    （``content`` 为空？``attachments`` 有值？）**在文档里没有明说**，
        #    只能靠实测确认。不记的话「发图没反应」永远只能猜。
        if message.attachments:
            _log.info(
                "收到带附件的 @ 消息：%d 个，类型 %s，正文 %d 字",
                len(message.attachments),
                [a.content_type for a in message.attachments],
                len(message.content.strip()),
            )

        if message.content.strip() == "":
            # 空消息时的随机回复候选，文案见 replies.json 的 bot.none_reply。
            # 刻意**不动**补参状态 —— 空消息通常只是误触，不该把用户正在补的
            # 参数丢掉（超时自会作废，见 liz_bot/pending.py）。
            #
            # ⚠️ **只发了一张图**（有附件、没正文）要单独回一条（2026-09-29 用户要求）：
            #    Liz **没有识图能力**，但「@ 了没反应」在本项目里是最容易被当成
            #    **崩溃**的现象（2026-09-28 那次「疑似崩溃」就是这个形状）。
            #    所以宁可明说看不见，也不要用 bot.none_reply 的随机寒暄 ——
            #    那等于**假装看见了**，用户会以为它能识图，下一条继续发图。
            if message.attachments:
                await message.reply(content=replies.text("bot.image_reply"))
            else:
                await message.reply(content=random.choice(replies.get("bot.none_reply")))
        else:
            # 会话键**提前算一次**：AI 分支与下面的指令分发都要用，
            # 而且必须算得**完全一样** —— 两处不一致的话，补参状态就会
            # 「写在 A 键上、查在 B 键上」，AI 照样抢走补参。
            session_key = _session_key(message)

            # ---- 群聊 AI 分支（2026-09-28）----
            # 范围：@ 机器人的群消息（本回调本身就是「群 @ 消息」事件）
            # 且**不是指令**、**且这个会话没在等补参/选候选**。
            # 三条判断全收在 ``ai_chat.should_handle`` 里（前缀从 command_router
            # 取常量、不硬编码），这样它可被自检覆盖 —— 见 _tools/test_ai_chat.py。
            #
            # ⚠️ 第 3 条（``has_pending``）是 2026-09-28 用户实测出来的冲突：
            #    补参状态是在下面 ``reply_text`` 里消费的，AI 若先抢走这条消息，
            #    「还差 1 个参数（难度）」之后回「紫」就永远补不上。
            #
            # ⚠️ 必须和指令路由**同进程同回调**：一个 AppID 只能有一条
            #    WebSocket 连接，AI 不能另起进程（见 Qwen3.7Flash可行性调研.md §3.1）。
            #
            # ⚠️ 未配置 key（``LIZ_AI_API_KEY``）时 ``should_handle`` 为假，
            #    整段跳过，行为与从前**完全一致**。
            if ai_chat.should_handle(message.content, session_key):
                # ⚠️ 会话键必须传进去：AI 的**滑动窗口**与**好感度**都按它索引
                #    （见 liz_bot/ai_context.py、liz_bot/affinity.py）。
                #    不传 ⇒ 每条消息都是单轮、且不记好感度（行为与从前一致）。
                answer = await ai_chat.reply(message.content, session_key)
                if answer is not None:
                    # ⚠️ 走 :meth:`_send_bubbles` 而不是 ``message.reply()``：
                    #    一次回复可能拆成 2~3 条短消息（2026-09-29 用户要求
                    #    「多回几条减少生硬感」）。失败兜底也收在里面。
                    await self._send_bubbles(message, answer)
                    return
                # AI 不可用 / 调用失败 ⇒ 落到下面走原逻辑，行为与从前一致

            elif ai_chat.should_swallow(message.content, session_key):
                # ⚠️ **AI 黑名单**（2026-09-29 用户要求）：直接吞掉，一个字都不回。
                #    为什么不只靠 should_handle 为假「让路」：让路会落到下面，
                #    而非指令消息在下面会被 reply_text 回一句
                #    「Liz 看不懂呢：「原文」」—— 等于把拉黑对象的话**回显**了一遍。
                #    判据（含「不吞补参回复」）见 ai_chat.should_swallow。
                _log.info(
                    "AI 黑名单命中，不接话：成员 %s",
                    session_key.rsplit(":", 1)[-1] if session_key else "-",
                )
                return

            try:
                # 前缀识别（`/` 本机指令 / `#` 舞萌命名空间）、解析、分发，
                # 全部在 command_router.reply_text 里 —— 本类只负责收发、
                # 补参会话键，以及「空消息」「发图」「异常」这三个外壳行为。
                #
                # ``rich=True`` 允许把结果渲染成图片：判定细节是 5 列表格，
                # 靠空格对齐在 QQ 的比例字体下**必然错位**，出图才看得清。
                reply = await reply_text(
                    message.content, session_key=session_key, rich=True,
                    notify=lambda text: self._notify(message, text))

                if isinstance(reply, RichReply):
                    await self._send_rich(message, reply)
                else:
                    await message.reply(content=reply)

            except Exception as e:
                _log.error(f"处理消息失败：{e}")
                await self._reply_error(message, e)

    async def _notify(self, message: GroupMessage, text: str) -> None:
        """任务**进行中**先发一条（目前只有 ``#上传`` 的预计等待时长）。

        为什么要这条通路：``#上传`` 真跑约 80 秒（60s 模拟游玩 + 16 次请求的
        节流），而 ``reply_text`` 是 ``await`` 到底才返回的 —— 没有它，用户
        在这 80 秒里收不到任何东西，只能干等（2026-09-27 用户要求补上）。

        ⚠️ **必须带 ``msg_id``** —— 不带就成了主动消息，要消耗群配额；
        这是**被动回复**，不花配额。``msg_seq`` 用 :data:`_NOTIFY_MSG_SEQ`（4）：
        1 留给最终结果、2 是错误提示、3 是发图降级，四个互不相同。

        本方法**刻意不抛异常**（调用方 `maimai_upload.handle_upload` 另有兜底）——
        预告发不出去只是少一句话，不该让整次上传失败。
        """
        await self.api.post_group_message(
            group_openid=message.group_openid,
            msg_id=message.id,
            msg_seq=_NOTIFY_MSG_SEQ,
            msg_type=0,
            content=text,
        )

    async def _send_bubbles(self, message: GroupMessage, text: str) -> None:
        """把 AI 的回复拆成 1~3 条短消息逐条发出。

        **为什么要拆**（2026-09-29 用户要求）：一次砸一整段很像机器，
        真人聊天是一条一条蹦的。拆开的规则在 :func:`ai_chat.split_bubbles`。

        ⚠️ **为什么用被动回复而不是「主动消息」**：主动消息虽然能发更多条，
        但有两个硬伤 ——
        ① 用户可以在 QQ 客户端关掉「允许主动发送」，关掉之后主动消息
           **一律发送失败**（官方文档原话），又一个**静默失败**；
        ② 消耗每群每日 1000 条的配额。
        而被动回复**每条 @ 最多能回 5 次**（官方文档：群聊 5 分钟 / 5 次），
        2~3 条气泡完全够用，还不花配额。

        ⚠️ **任一条发不出去就中止剩下的**，并补一条固定推托
        （:meth:`_send_safe`，``msg_seq=5``）—— 半截话比不说更难受，
        而且「说不出话」正是本项目最容易伪装成「崩溃」的现象。

        本方法**刻意不抛异常**：失败已经兜住了，再往外抛只会让外层
        当成「处理消息失败」并再回一句错误提示，同一件事报两次。
        """
        bubbles = ai_chat.split_bubbles(text)
        if not bubbles:
            return

        for index, bubble in enumerate(bubbles):
            if index:
                # 让气泡之间有个人味儿的间隔（见 _BUBBLE_GAP_SECONDS）
                await asyncio.sleep(_BUBBLE_GAP_SECONDS)
            try:
                await self.api.post_group_message(
                    group_openid=message.group_openid,
                    msg_id=message.id,
                    msg_seq=_BUBBLE_MSG_SEQ_BASE + index,
                    msg_type=0,
                    content=bubble,
                )
            except Exception:
                # ⚠️ **合规兜底第 3 层**（2026-09-29）。发不出去 = 平台把这条
                #    内容拒了（或网络真挂了）。补一条**固定的、安全的**推托
                #    —— 它是常量、不含 URL、不含敏感词，所以不会二次被拒。
                #    网络真挂时这条同样发不出去，日志里能看到两条失败。
                _log.exception(
                    "AI 回复发送失败，改发固定推托（第 %d/%d 条）",
                    index + 1,
                    len(bubbles),
                )
                await self._send_safe(message, replies.text("ai.reply_rejected"))
                return

    async def _send_safe(self, message: GroupMessage, text: str) -> None:
        """补发一条**固定文案** —— 内容合规兜底的最后一层。

        触发点只有一个：AI 的回复 ``message.reply()`` 抛了异常。
        最常见的成因是**平台把那条内容拒了**（敏感内容），其次是网络抖动。

        ⚠️ **为什么不去分辨错误码**：botpy 在非 2xx 时只把响应体的 ``message``
        透出来（见 ``botpy/http.py`` 的 ``_handle_response``），QQ 的业务码
        （``40034006`` 之类）**拿不到**。所以这里不猜 —— 任何发送失败都补一条。
        这样做的代价很小：**网络真挂时这条同样发不出去**（同一个连接），
        只有「网络正常但内容被拒」才会真的补上，行为正好是我们想要的。

        ⚠️ ``text`` **必须是常量**（``replies.json`` 的 ``ai.reply_rejected``）——
        把模型原文拼进来就等于把被拒的东西再发一遍。

        ⚠️ ``msg_seq`` 用 :data:`_SAFE_FALLBACK_MSG_SEQ`（5）：``seq=1`` 刚才
        已经试过并失败了，「相同的 msg_id + msg_seq 重复发送会失败」。

        本方法**刻意不抛异常**（同 :meth:`_notify`）—— 兜底都失败了就没辙了，
        再往外抛只会让外层当成「处理消息失败」并再回一句错误提示。
        """
        try:
            await self.api.post_group_message(
                group_openid=message.group_openid,
                msg_id=message.id,
                msg_seq=_SAFE_FALLBACK_MSG_SEQ,
                msg_type=0,
                content=text,
            )
        except Exception:
            _log.exception("合规兜底文案也发送失败")

    async def push_proactive(self, group_openid: str, text: str) -> bool:
        """**主动推送**一条消息到群（不带 ``msg_id``）—— 2026-09-29。

        ⚠️⚠️ **这条路和被动回复不是一回事**。它需要**群主在手机 QQ 里打开**
        「机器人主动在群聊内发言」（群聊 → 设置 → 机器人）。没开的话网关返
        ``40034105 主动消息失败, 无权限`` —— 本方法据此打一条**说清原因**的日志，
        不然运维只会看到一句「发不出去」然后去查错方向。

        ⚠️ 还有一条**官方原话的静默失败**：群成员可以关掉「接收主动消息」，
        关掉之后主动消息一律失败。⇒ 主动推送是**尽力而为**，不是可靠通道。

        ⚠️ 为什么不用 ``message.reply()`` / ``message.reply(content=...)``：
        那些都会带上 ``msg_id``，就又变成被动回复了。主动推送**必须**走裸的
        ``post_group_message``，**不传 ``msg_id``、也不传 ``msg_seq``**
        （``msg_seq`` 只在和 ``msg_id`` 联用时才有意义）。

        ⚠️ 调用方是 :mod:`liz_bot.proactive` 的定时器 —— 默认不启动，
        两个环境变量都配了才会跑到这里。

        本方法**刻意不抛异常**（同 :meth:`_notify`）—— 推送失败只是少一句话，
        不该把定时器循环带崩。

        :returns: 成功 ``True``，失败 ``False``。
        """
        if not text:
            return False
        try:
            await self.api.post_group_message(
                group_openid=group_openid,
                msg_type=0,
                content=text,
            )
            return True
        except Exception as exc:
            detail = str(exc)
            if "40034105" in detail or "无权限" in detail:
                _log.error(
                    "主动推送被拒（40034105）—— 群主没开「机器人主动在群聊内发言」。"
                    "路径：手机 QQ → 群聊 → 设置 → 机器人。%s",
                    detail[:200],
                )
            else:
                _log.exception("主动推送失败：%s", detail[:200])
            return False

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