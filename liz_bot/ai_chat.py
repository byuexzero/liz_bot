"""群聊 AI 回复 —— Qwen3.7-Flash。

做六件事：

1. **接一条消息、回一句话** —— 调用方（``qqgroupbot``）已经把范围限死在
   「群 @ 消息」上，所以这里不再判断触发条件。
2. **带滑动上下文窗口** —— 最近 10 轮，见 :mod:`liz_bot.ai_context`。
   旧原型把**完整历史每轮全发**，聊到第 50 轮单次输入 20K+ token、
   费用涨 5~10 倍 ⇒ **窗口是账单闸门，不是体验优化**。
3. **按好感度调冷暖** —— 每会话一个 txt，见 :mod:`liz_bot.affinity`。
4. **调一次百炼** —— 走 OpenAI 兼容接口，用 ``aiohttp`` 直发。
5. **强制 URL 过滤** —— QQ 开放平台对含 URL 的消息**直接拒发**（错误码
   ``40054010``），而模型特别爱输出链接。所以过滤必须落在代码里，
   **不能指望提示词管住它**：提示词是建议，这里是保证。
6. **内容合规兜底** —— 见下节。合规拦截的**表现**是「机器人突然不理人」，
   而它其实是**两个完全不同的故障**，分别落在本模块与 ``qqgroupbot``。

内容合规兜底（2026-09-29）
--------------------------
「Liz 突然不回话了」在日志里长这样（2026-09-28 实测），而**容器根本没崩**
（重启次数 0 / 退出码 0 / 非 OOM）::

    23:33:16  输入 1250 token
    23:34:29  ❌ HTTP 400 data_inspection_failed
    23:35:05  ❌ HTTP 400 data_inspection_failed

三层兜底，缺一层就会退化成「沉默」：

* **第 1 层 · 输入侧（本模块）**：百炼判定输入违规 ⇒ 丢窗口重试；
  仍被拦 ⇒ 回 ``ai.reply_blocked``。**绝不能返回 ``None``**。
* **第 2 层 · 输出侧（本模块）**：输出被拦时 ``_post`` 同样返回
  ``inspection``，走的是同一条路 —— 丢窗口重试没有用，但**推托文案照发**。
* **第 3 层 · 发送侧（``qqgroupbot._send_safe``）**：QQ 平台把消息拒了
  （botpy 的异常**只透出 ``message``、拿不到业务码**，所以不去猜错误码，
  任何发送失败都补一条固定文案）。网络真挂时这条也发不出去 —— 日志里有两条。

⚠️ 兜底文案必须是**常量**（``replies.json`` 的 ``ai.reply_blocked`` /
``ai.reply_rejected``）：把模型原文拼进去 = 把被拒的东西再发一遍。

设计原则
--------
* **失败一律静默降级**：任何异常都吞掉、返回 ``None``，让调用方走原来的逻辑。
  AI 是锦上添花，不该因为它挂了就让人连 ``/help`` 都用不了。
* **不加新依赖**：用项目已有的 ``aiohttp``。引 ``openai`` SDK 会让
  ``requirements.txt`` 变化 ⇒ Docker 的 ``COPY requirements.txt`` 层缓存失效
  ⇒ 部署要重跑 apt + pip。
* **强制关闭思考模式**：见 :data:`_THINKING_NOTE`。
* **人设不硬编码**：提示词放 ``replies.json`` 的 ``ai.system_prompt``，
  改人设下一条消息就生效（mtime 热更新），不用走「提交 → 部署」。
  调人设必然要反复试，热更新省掉的正是最烦的那一步。
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from datetime import date
from typing import NamedTuple

import aiohttp
from botpy import logging

from liz_bot import affinity, ai_blacklist, replies
from liz_bot.ai_context import STORE as _history

# ⚠️ 这一行**只为副作用**：``liz_bot.config`` 在模块级把项目根的 ``.env``
# 灌进 ``os.environ``（容器里没有该文件，是 no-op）。
# 少了它，单独跑本模块（比如测试脚本）时 ``LIZ_AI_API_KEY`` 不会进环境变量，
# ``available()`` 会莫名其妙返回 False。
from liz_bot import config as _config  # noqa: F401  (仅为触发 .env 加载)

#: 指令前缀与等待状态。**都从 ``command_router`` 取，不在这里硬编码** ——
#: 触发判断是「AI 会不会抢走指令」的唯一防线，前缀写错就是群里指令失灵。
#: （``command_router`` 不反向 import 本模块，无循环。）
from liz_bot.command_router import (
    PREFIX_MAIMAI,
    PREFIX_NORMAL,
    has_pending,
)
from liz_bot.text_layout import display_width

_log = logging.get_logger()

#: 默认接口地址。
#:
#: ⚠️ 必须是 ``dashscope.aliyuncs.com``。百炼控制台里给的「专属实例」地址
#: （``llm-xxxx.cn-beijing.maas.aliyuncs.com``）用这把 key 会 **403
#: ``Workspace endpoint access denied``**（2026-09-28 实测）。
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

#: 默认模型。
DEFAULT_MODEL = "qwen3.7-flash"

#: 单次回复的最大输出 token。
#:
#: 群聊不需要长文；这个值同时是**成本闸门** —— 万一模型跑飞，单次最多也就
#: 烧掉 ``320 × 0.8 / 1e6 ≈ 0.00026`` 元。
#:
#: ⚠️ 2026-09-29 从 400 降到 320：人设变长后要重新算总账（见
#: :data:`DEFAULT_MAX_CALLS_PER_DAY` 的推导）。320 token ≈ 200 个汉字，
#: 仍是人设要求的「十几个到三十来个字」的 6 倍以上 —— 不会截断正常回复。
MAX_TOKENS = 320

#: 采样温度。qwen 默认 1.0，这里略调高，**目的是打破句式坍缩**。
#:
#: ⚠️ 2026-09-29 实测（用户反馈「回答过于模板」）：默认温度下 6 条**完全不同**的
#: 输入，回复**全部以「呢」结尾、半数以「嗯」开头**，句式坍缩成 ``[嗯，]X呢``。
#: 更糟的是**窗口里那些同款回复会变成新的示例** ⇒ 正反馈，越聊越模板。
#: 解药是三件事一起做：**示例多样化**（见 replies.json）+ 放开长度 + 这个温度。
TEMPERATURE = 1.1

#: 单次请求超时（秒）。
#:
#: 被动消息的有效期是 5 分钟，给足余量但仍然要有上限 —— 否则一次卡死
#: 会一直占着 :data:`MAX_CONCURRENCY` 的名额。
REQUEST_TIMEOUT = 45.0

#: 同时进行的 AI 请求上限。防止有人刷屏时把并发打满。
MAX_CONCURRENCY = 3

_SEM = asyncio.Semaphore(MAX_CONCURRENCY)

#: 单次请求的**输入**上限（字符）。**它是成本公式里的那个变量。**
#:
#: 百炼是**阶梯计费**：单次输入一旦超过 32K token，**整单**单价涨 3 倍
#: （¥0.2 → ¥0.6 / 百万）。这里的上限远低于 32K，防的是另一件事 ——
#: **单次调用的绝对花费**（见 :data:`DEFAULT_MAX_CALLS_PER_DAY` 的推导）。
#:
#: ⚠️ 2026-09-29 从 20000 收到 9000。人设扩到 1789 字、窗口上限 5000 字之后，
#: 9000 这个数把「人设 + 满窗口 + 一条 2K 字的长消息」刚好装下，
#: 同时把单次最坏花费钉在 ¥0.0016 —— 这是 200 次/天 能守住 10 元的前提。
#:
#: ⚠️ 超限**不再静默**：先丢历史重试（多数情况够），仍超才回固定推托
#: （见 :func:`reply`）。收小这个值会让「贴长文」更容易撞上限，所以必须给出口。
MAX_PROMPT_CHARS = 9000

#: 每日 AI 调用次数上限（全局）。**这是「月成本 ≤ 10 元」的保证。**
#:
#: 推导（2026-09-29 重算，人设扩长 + 窗口开大之后）：
#:
#: * 最坏单次输入 = :data:`MAX_PROMPT_CHARS` 9000 字 ÷ 1.30 字/token ≈ **6923 token**
#:   （1.30 是实测比值：人设 1053 字时输入 817 token）
#: * 最坏单次输出 = :data:`MAX_TOKENS` = 320 token
#: * 最坏单次花费 = 6923×0.2e-6 + 320×0.8e-6 = **¥0.00164**
#: * 200 次/天 × 30 天 × 0.00164 = **¥9.84/月** ✅
#:
#: ⚠️ 这是**最坏情况**（每条都贴满长文 + 模型每次都写满）。真实场景：
#: 人设 1377 + 窗口约 600 + 本条 30 ≈ 2000 token，输出 ~15 token
#: ⇒ 单次约 ¥0.00042 ⇒ 200 次/天 也只 **¥2.5/月**。
#:
#: ⚠️ 它的作用只有一个：**有人刷屏或模型陷入循环时，把账单钉死**。
#: 可用 ``LIZ_AI_MAX_CALLS_PER_DAY`` 覆盖。
DEFAULT_MAX_CALLS_PER_DAY = 200

#: 单个会话的每日上限。防止**一个人**把全局额度刷完，让别人当天没得用。
DEFAULT_MAX_CALLS_PER_SESSION = 40


def _int_env(name: str, default: int) -> int:
    """读一个正整数环境变量；缺失 / 非法 / ≤0 时用默认值。"""
    raw = (os.environ.get(name) or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return default


_lock_budget = threading.Lock()


class _DailyBudget:
    """按**自然日**计数的调用闸门。**线程安全**（asyncio 单线程，仍加锁兜底）。

    刻意做成「预留式」：:meth:`allow` 在**调用前**就把名额扣掉。
    否则并发时两个协程可能都读到「还剩 1 次」然后各调一次。
    """

    def __init__(self, total: int, per_session: int):
        self.total = total
        self.per_session = per_session
        self._day: date | None = None
        self._used = 0
        self._by_session: dict[str, int] = {}

    def _roll(self) -> None:
        """跨天则清零。**调用方必须已持锁**。"""
        today = date.today()
        if self._day != today:
            self._day = today
            self._used = 0
            self._by_session.clear()

    def allow(self, key: str | None) -> bool:
        """还有额度吗？**有就顺手扣掉**（返回值即「已预留」）。"""
        with _lock_budget:
            self._roll()
            if self._used >= self.total:
                return False
            if key and self._by_session.get(key, 0) >= self.per_session:
                return False
            self._used += 1
            if key:
                self._by_session[key] = self._by_session.get(key, 0) + 1
            return True

    def snapshot(self) -> tuple[int, int]:
        """``(已用, 上限)`` —— 给日志与测试看。"""
        with _lock_budget:
            self._roll()
            return self._used, self.total


_lock_budget = __import__("threading").Lock()

#: 全局预算闸门。模块级单例（``test_ai_chat`` 会重置它）。
BUDGET = _DailyBudget(
    _int_env("LIZ_AI_MAX_CALLS_PER_DAY", DEFAULT_MAX_CALLS_PER_DAY),
    _int_env("LIZ_AI_MAX_CALLS_PER_SESSION", DEFAULT_MAX_CALLS_PER_SESSION),
)

#: 主动推送占用的**会话键**（见 :func:`proactive_line`）。
#:
#: 它不是真的会话 —— 只是给 :data:`BUDGET` 一个记账用的名字，
#: 顺便让日志里的「成员」字段一眼能看出这是主动推送而不是某人发的消息。
_PROACTIVE_BUDGET_KEY = "__proactive__"


def build_system_prompt(session_key: str | None = None) -> str:
    """拼出这次的系统提示 —— **人设 + 当前关系**。

    人设**每次现取** ``replies.text("ai.system_prompt")``，不在模块级冻结：
    类属性 / 模块常量会在 import 时求值，改文案就不生效了
    （同 ``ai.url_removed`` 的处理，见 ``liz_bot/replies.py`` 的热更新说明）。

    :param session_key: 会话键；给了就附加一行好感度与风格带（用户看不到）。
    """
    base = replies.text("ai.system_prompt")
    if not session_key:
        return base

    value = affinity.load(session_key)
    bands = replies.get("ai.style_bands")
    style = bands[affinity.band(value)]
    note = replies.text("ai.affinity_note", value=value, style=style)
    return f"{base}\n\n{note}"

#: ⚠️ 为什么必须显式关思考模式。
#:
#: 2026-09-28 实测：``qwen3.7-flash`` 的思考模式**默认是开的**。
#: 同一句「只回三个字：能听见」：
#:
#: * 不传 ``enable_thinking`` ⇒ 输出 164 token（其中 **160 是思维链**）
#: * 传 ``enable_thinking: false`` ⇒ 输出 **2** token
#:
#: **差 82 倍**。而思维链按**输出价**（0.8 元/百万）计费 —— 忘了关就是白烧钱，
#: 还会让 ``max_tokens`` 被思维链吃光、正式回复被截断。
_THINKING_NOTE = "见模块 docstring 与 MAX_TOKENS 附近的说明"


class _Settings(NamedTuple):
    """AI 配置。缺 ``api_key`` 时整个功能不可用（但机器人照常启动）。"""

    api_key: str
    base_url: str
    model: str


#: 惰性缓存的配置，见 :func:`_settings_or_none`。
_settings: _Settings | None = None


def _settings_or_none() -> _Settings | None:
    """取（必要时读取）AI 配置；未配置时返回 ``None``。

    ⚠️ **只在成功时缓存**：首次调用若因为环境变量还没就绪而返回 ``None``，
    下一次仍会重新读 —— 否则一次「读早了」就会让 AI 永久不可用。
    """
    global _settings
    if _settings is not None:
        return _settings

    api_key = (os.environ.get("LIZ_AI_API_KEY") or "").strip()
    if not api_key:
        return None

    _settings = _Settings(
        api_key=api_key,
        base_url=(
            os.environ.get("LIZ_AI_BASE_URL") or DEFAULT_BASE_URL
        ).strip().rstrip("/"),
        model=(os.environ.get("LIZ_AI_MODEL") or DEFAULT_MODEL).strip(),
    )
    return _settings


def available() -> bool:
    """AI 功能是否可用（``LIZ_AI_API_KEY`` 已配置）。"""
    return _settings_or_none() is not None


def is_command(content: str) -> bool:
    """这条消息是否属于指令命名空间（``/`` 或 ``#`` 开头）。

    允许前导空白（群里复制粘贴常带空格），与 ``command_router`` 的解析口径一致。

    :param content: 消息正文（@ 部分已由平台剥离）。
    """
    return content.lstrip().startswith((PREFIX_NORMAL, PREFIX_MAIMAI))


def is_blacklisted(session_key: str | None) -> bool:
    """这个会话是否在 **AI 黑名单**上（见 :mod:`liz_bot.ai_blacklist`）。

    抽成函数而不是让 ``qqgroupbot`` 直接 import ``ai_blacklist``：
    调用方只需要「问 ai_chat」一件事，名单的存储位置不该扩散出去。
    """
    return ai_blacklist.is_blocked(session_key)


def should_swallow(content: str, session_key: str | None) -> bool:
    """黑名单要不要**直接吞掉**这条消息（一个字都不回）。

    三条同时成立才吞：

    1. 在黑名单上；
    2. **不是指令**；
    3. **这个会话没在等补参**。

    ⚠️ 为什么不能只靠 ``should_handle`` 为假来「让路」：让路只会**落到下面**，
    而下面会把非指令消息交给 ``reply_text`` ⇒ 回一句
    「Liz 看不懂呢：「原文」」—— 那等于**把拉黑对象的话回显了一遍**，
    既不是拉黑，还白搭一条消息。

    ⚠️ 第 3 条不能少（否则就是个隐蔽的坑）：拉黑是「不和 Liz 聊天」，
    不是「封禁这个人」—— 他完全可以照常用 ``/估分``。而**补参回复**
    （机器人问「难度？」他回「紫」）**长得就是一条闲聊**，少了这一条
    就会被吞掉，那条指令永远补不齐，而且不报错。

    :param content: 消息正文。
    :param session_key: 会话键（``群:成员``）。
    """
    return (
        is_blacklisted(session_key)
        and not is_command(content)
        and not has_pending(session_key)
    )


def should_handle(content: str, session_key: str | None = None) -> bool:
    """该不该把这条群消息交给 AI。

    四条同时成立才接管：

    1. 已配置 key（:func:`available`）；
    2. **不是指令**（:func:`is_command`）；
    3. **该会话没有正在等的补参 / 选候选**（``command_router.has_pending``）；
    4. **该会话不在 AI 黑名单上**（:func:`is_blacklisted`）。

    第 3 条是 2026-09-28 用户实测出来的：AI 分支跑在 ``reply_text`` **之前**，
    而补参状态是在 ``reply_text`` 里消费的 —— 少了它，
    「还差 1 个参数（难度）～」之后用户回「紫」，会被 AI 当成闲聊回一句，
    补参永远凑不齐（消歧时回序号 ``1`` 也一样被抢）。等待状态有 60 秒 TTL
    （见 ``liz_bot.pending``），所以 AI 最多让路 60 秒，之后照常接管。

    第 4 条是 2026-09-29 用户要求补的（「加入 aichat 黑名单」）。
    ⚠️ 它只让 **AI 让路**，不影响指令 —— 调用方还要再判断一次
    「黑名单 + 非指令 ⇒ 直接不接话」，否则会掉进 ``bot.not_command``
    的「Liz 看不懂呢：「原文」」，等于**把对方的话回显了一遍**。

    抽成函数是为了**可测**：这是唯一「写反了也不报错、只在群里表现异常」
    的判断 —— 过松会让 AI 抢走指令或补参，过严则 @ 了没反应。
    调用方（``qqgroupbot``）只负责把范围限死在「群 @ 消息」上。

    :param content: 消息正文。
    :param session_key: 会话键（``群:成员``）。**不给就当作没有等待状态** ——
        拿不到会话键时补参本来也不工作（见 ``qqgroupbot._session_key``）。
    """
    return (
        available()
        and not is_command(content)
        and not has_pending(session_key)
        and not is_blacklisted(session_key)
    )


# ---------------------------------------------------------------------------
# URL 过滤
# ---------------------------------------------------------------------------
#: URL 的**终止字符** —— 空白 + 常见中文标点。
#:
#: ⚠️ 不能只用 ``\S+``：中文标点不是空白，``见 https://a.com，然后`` 会把
#: 「，然后」一起吞进 URL，替换后句子就残了。
#:
#: ⚠️ ``|`` / ``｜`` 是 2026-09-29 加进来的，为的是 :data:`BUBBLE_SEPARATOR`：
#: 模型若把分隔符**紧贴**在链接后面（``https://a.com|||下一句``），
#: 竖线不在终止集里就会被当成 URL 的一部分**连「下一句」一起删掉** ——
#: 分隔符没了、正文也残了，而且**不报错**。URL 里本来也不会出现裸竖线
#: （真要写会编码成 ``%7C``），所以加进去是纯收益。
_URL_STOP = r"\s，。！？、；：“”‘’（）【】《》…—～·|｜"

#: markdown 链接 ``[文字](url)`` —— 只留文字。
#:
#: **必须先于** :data:`_URL_RE` 执行，否则会留下
#: ``[文字]([链接已省略])`` 这种残骸。
_MD_LINK_RE = re.compile(
    r"\[([^\]]*)\]\(\s*(?:https?://|www\.)[^)]*\)", re.IGNORECASE
)

#: 带协议的链接：``http(s)://`` 与 ``www.`` 开头。
#:
#: ⚠️⚠️ 字符类是**取反**的（``[^...]``）—— :data:`_URL_STOP` 列的是
#: 「URL 到哪里算结束」，**不是**「URL 由哪些字符组成」。
#: 写成 ``[...]`` 会让整个正则失效（变成「匹配一串标点」），
#: 实测症状：``https://maimai.com`` 只把域名换掉、**``https://`` 被留下**，
#: 而 QQ 照样会因此拒发。
_URL_RE = re.compile(r"(?:https?://|www\.)[^" + _URL_STOP + r"]+", re.IGNORECASE)

#: 裸域名：``example.com`` / ``a.b.cn/path``。
#:
#: 按**常见 TLD 白名单**匹配，把误伤压到最低 —— 没有白名单的话，
#: 「1.5 版」「maimai.py」这类普通文本都会被当成域名换掉。
_TLDS = (
    "com|cn|net|org|io|me|tv|cc|top|xyz|dev|app|ai|info|biz|edu|gov|co"
    "|uk|jp|us|hk|tw|mo|ru|de|fr"
)
_BARE_DOMAIN_RE = re.compile(
    r"\b[\w-]+(?:\.[\w-]+)*\.(?:" + _TLDS + r")\b(?:/[^" + _URL_STOP + r"]*)?",
    re.IGNORECASE,
)


def sanitize(text: str) -> str:
    """把模型输出里的 URL 全部拿掉。**这一步不能省**。

    QQ 开放平台对含 URL 的消息**直接拒发**（``40054010``），而模型在中文闲聊
    场景里非常爱塞链接。三级处理，**顺序不能反**：

    1. markdown 链接 ``[文字](url)`` → 只留文字
    2. 带协议的 ``http(s)://`` / ``www.``
    3. 裸域名（按 TLD 白名单）

    :param text: 模型原始输出。
    :returns: 过滤后的文本（已 strip）。
    """
    placeholder = replies.text("ai.url_removed")

    out = _MD_LINK_RE.sub(r"\1", text)
    out = _URL_RE.sub(placeholder, out)
    out = _BARE_DOMAIN_RE.sub(placeholder, out)

    # 模型有时连着列好几个链接 —— 把挨在一起的占位符合并成一个
    if placeholder:
        out = re.sub(
            "(?:" + re.escape(placeholder) + r"[\s、,，]*){2,}", placeholder, out
        )
    return out.strip()


# ---------------------------------------------------------------------------
# 多气泡（把一次回复拆成几条短消息发）
# ---------------------------------------------------------------------------
#: 一次回复最多拆几条。
#:
#: ⚠️ **上限不是随便定的**：群聊被动回复「每条消息可回复次数」官方上限就是 **5**
#: （见 QQ 开放平台《消息收发概述》频率与时效规则，群聊 5 分钟 / 5 次）。
#: 而 ``msg_seq`` 的分配是 **1~3 给气泡、4 给 `#上传` 预告、5 给合规兜底**
#: （见 ``qqgroupbot`` 的常量）。再加气泡就得动兜底那一格 —— 不值当。
MAX_BUBBLES = 3

#: 短于这个**显示宽度**（CJK 算 2 格）的回复**不拆**。
#:
#: 它拦的是「很短但不止一句」的回复 —— 「嗯。好。」「好。谢谢。」拆开就只剩
#: 标点和单字了。**单句回复不靠它拦**（那种由 :func:`split_bubbles` 的
#: 「句子数 < 2」判掉）。
#:
#: 定 20 的依据：人设的示例里，**两句话**的回复宽度都在 22~42，
#: 一句话的都在 8~30（但句子数是 1）。20 正好把前者全放过去、把
#: 「嗯。好。」这类挡在外面。
MIN_SPLIT_WIDTH = 20

#: 句末标点 —— 拆气泡在这里切。
_SENTENCE_END = "。！？…～"

#: 一句话：**至少一个非句末标点的字符**，后面跟任意个句末标点。
#:
#: ⚠️ 不能直接按 :data:`_SENTENCE_END` 逐字切：``……`` 会被切成两个
#: 纯标点的碎片（``一百五十七。……这也算数吗？`` ⇒ 3 段，中间那段是「……」）。
#: 本正则只匹配「有内容的段」，**夹在中间和被跳过的纯标点由
#: :func:`_split_sentences` 补回来**（不然会**静默吞掉**开头的「……」）。
_SENTENCE_RE = re.compile(r"[^" + _SENTENCE_END + r"]+[" + _SENTENCE_END + r"]*")

#: 模型自己写的**气泡分隔符**（2026-09-29 用户要求）。
#:
#: 语义是「**模型建议在哪切**」，**不是**「模型决定拆几条」—— 切完照样要过
#: :func:`split_bubbles` 的裁决（合并纯标点段、裁到 :data:`MAX_BUBBLES`）。
#: 为什么必须留这层裁决：``msg_seq`` 只有 1~5 五个坑位（见 ``qqgroupbot``
#: 的分配表），模型想拆六条也只能砍到三条。
#:
#: ⚠️ 为什么用 ``|||`` 而不是换行：换行会和「模型自己分行」那条老路径混在一起，
#: **漏解析了也看不出来**（换行本来就允许）。``|||`` 是显式的，不解析就是 bug。
#: ⚠️ 为什么人设里**只写规则、示例区一个字都不加**：本项目在「**示例的权重压倒
#: 规则**」上栽过两次 —— 示例里出现一次分隔符，就会退化成「每条都拆三条」。
BUBBLE_SEPARATOR = "|||"

#: 分隔符的**宽松匹配**：2 个及以上的竖线（半角/全角混用、中间夹空格都算）。
#:
#: ⚠️ 用「2+」而不是死磕 3：模型写 ``||``（少一根）、``｜｜｜``（全角）、
#: ``| | |``（夹空格）都该被认出来。**单个 ``|`` 不算** —— 太常见，容易误伤。
#: ⚠️ 必须是**非捕获组**：带捕获组的正则在 :func:`re.split` 里会把分隔符本身
#: 也塞进结果列表。
_BUBBLE_SEP_RE = re.compile(r"[|｜]\s*(?:[|｜]\s*)+")


def _split_on_separator(text: str) -> list[str]:
    """按模型写的分隔符切开，并**吃掉所有分隔符**。

    返回单元素列表表示「模型没写分隔符」或「写了但只切出一段」，
    调用方据此回退到确定性均分。

    ⚠️ **必须吃掉全部**：残留一个 ``|||`` 就会被当成正文发到群里。
    :func:`re.split` 在这里是安全的 —— 每一处出现都会被切开。
    """
    if not _BUBBLE_SEP_RE.search(text):
        return [text]
    return [part.strip() for part in _BUBBLE_SEP_RE.split(text)]


def _merge_orphans(parts: list[str]) -> list[str]:
    """把「纯标点」的段并回**相邻**的段（``……`` 这类）。"""
    out: list[str] = []
    for part in parts:
        if out and not part.strip(_SENTENCE_END):
            out[-1] += part
        else:
            out.append(part)
    # 开头的纯标点没有「前一段」可并（``……这也算数吗？``）⇒ 并进后一段
    if len(out) > 1 and not out[0].strip(_SENTENCE_END):
        out[1] = out[0] + out[1]
        out.pop(0)
    return out


def _split_sentences(text: str) -> list[str]:
    """按句末标点切成句子（每段自带句末标点，纯标点段已并回）。

    ⚠️ 必须**补回正则跳过的部分**：:data:`_SENTENCE_RE` 要求「有内容」，
    所以整段开头/中间的纯标点不在任何 match 里。不补的话
    ``……这也算数吗？`` 会变成 ``这也算数吗？`` —— **把 Liz 的语气吞掉**，
    而且不报错。
    """
    parts: list[str] = []
    pos = 0
    for match in _SENTENCE_RE.finditer(text):
        if match.start() > pos:
            parts.append(text[pos:match.start()] + match.group(0))
        else:
            parts.append(match.group(0))
        pos = match.end()
    if pos < len(text):
        parts.append(text[pos:])
    return _merge_orphans(parts)


def _pack(sentences: list[str], limit: int) -> list[str]:
    """把句子**按宽度均分**成 ``limit`` 段（连续、不重排）。

    「均分」而不是「凑够一段再开下一段」：后者会让最后一段只剩一两个字，
    发出来像个半截句子，比不分还生硬。
    """
    count = min(limit, len(sentences))
    if count <= 1:
        return ["".join(sentences)]

    total = sum(display_width(s) for s in sentences)
    target = total / count

    out: list[str] = []
    buf: list[str] = []
    width = 0
    for index, sentence in enumerate(sentences):
        remaining_sentences = len(sentences) - index
        remaining_slots = count - len(out)
        # 剩下的句子必须够填满剩下的段，否则提前收尾（防止末段为空）
        must_close = remaining_sentences <= remaining_slots
        buf.append(sentence)
        width += display_width(sentence)
        if must_close or (width >= target and len(out) < count - 1):
            out.append("".join(buf))
            buf, width = [], 0
    if buf:
        out.append("".join(buf))
    return out


def split_bubbles(text: str, limit: int = MAX_BUBBLES) -> list[str]:
    """把模型的一次回复拆成 1~``limit`` 条短消息。

    **为什么要拆**（2026-09-29 用户要求）：一条长回复砸过去很像机器；
    真人聊天是一条一条蹦的。「多回几条」能明显减少生硬感。

    三条路径，**按优先级**：

    1. 模型自己写了 :data:`BUBBLE_SEPARATOR` ⇒ **按它给的位置切**。
       切完仍要过裁决（合并纯标点段、裁到 ``limit``）——
       模型只能**建议**在哪切，拆几条永远是这里说了算。
    2. 模型自己写了多行 ⇒ **逐行就是气泡**，超出的并进最后一条。
    3. 只有一行 ⇒ 短于 :data:`MIN_SPLIT_WIDTH` 不拆；够长就按句末标点
       **均分**成 2~3 段。

    ⚠️ **路径 1 是「加分项」不是「必答题」**：模型没写分隔符时，行为与从前
    **一字不差**。所以这条新路径最坏的结果只是「模型不用它」，不会让回复变差。
    也**不套** :data:`MIN_SPLIT_WIDTH` —— 模型显式分了「在的。」/「怎么了？」
    这种短句是对的，用宽度去合并反而是错的。

    ⚠️ 纯函数、不联网、不抛异常 —— 拆错了最多是语气怪一点，绝不该让回复发不出去。

    :param text: 模型输出（已过 :func:`sanitize`）。
    :param limit: 最多几条。
    :returns: 非空字符串列表；输入为空时返回 ``[]``（调用方据此跳过发送）。
    """
    if limit < 1:
        limit = 1

    # ① 模型自己标了切点（2026-09-29）
    parts = [part for part in _split_on_separator(text) if part]
    if len(parts) > 1:
        parts = _merge_orphans(parts)
        if len(parts) > limit:
            parts = parts[: limit - 1] + ["\n".join(parts[limit - 1:])]
        return parts
    # 没写分隔符，或写了但只切出一段 ⇒ 用**已剥掉分隔符**的正文走老逻辑。
    # ⚠️ 不能继续用原 `text`：``|||a`` 这种会把 ``|||`` 当正文发出去。
    text = "".join(parts) if parts else text

    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    if not lines:
        return []

    if len(lines) > 1:
        # 模型自己分了行 —— 尊重它，只做「别超过上限」
        if len(lines) <= limit:
            return lines
        return lines[: limit - 1] + ["\n".join(lines[limit - 1:])]

    single = lines[0]
    if display_width(single) <= MIN_SPLIT_WIDTH:
        return [single]

    sentences = _split_sentences(single)
    if len(sentences) < 2:
        return [single]

    bubbles = _pack(sentences, limit)
    return [b for b in (b.strip() for b in bubbles) if b] or [single]


def _member_of(session_key: str | None) -> str:
    """会话键里的**成员 openid** —— 日志用。

    ⚠️ 为什么专门记它：**这是拿到 openid 的唯一途径**。
    QQ 开放平台不暴露 QQ 号，而配 AI 黑名单要的正是 openid
    （见 :mod:`liz_bot.ai_blacklist`）。不记的话运维只能靠猜。
    """
    return session_key.rsplit(":", 1)[-1] if session_key else "-"


def _extract(data: dict, session_key: str | None = None) -> str:
    """从响应体里取出回复文本，并记一行 token 用量。

    记用量是为了**能盯成本** —— 万一哪天忘了关思考模式，日志里
    ``思维链`` 那个数会立刻涨到几百，一眼就能看出来。

    :param session_key: 只用于日志末尾的「成员 openid」（见 :func:`_member_of`）。
    """
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        _log.warning("AI 返回结构异常：%s", str(data)[:200])
        return ""

    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    _log.info(
        "AI 用量：输入 %s / 输出 %s token（其中思维链 %s）· 成员 %s",
        usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
        details.get("reasoning_tokens", 0),
        _member_of(session_key),
    )
    return (message.get("content") or "").strip()


def _prompt_chars(messages: list[dict]) -> int:
    """这次请求的输入总字符数（成本闸门看的就是它）。"""
    return sum(len(m["content"]) for m in messages)


def _build_messages(
    content: str, session_key: str | None, *, use_history: bool = True
) -> list[dict]:
    """拼出这次的 ``messages`` —— 系统提示 + 滑动窗口 + 本条消息。

    ⚠️ **本条消息不在窗口里**：窗口是在**一次成功的往返之后**才成对追加的
    （见 :func:`reply`），所以这里直接 append 不会重复。

    :param use_history: 关掉就是「只带人设 + 本条」—— 内容审核自愈时用（见 :func:`reply`）。
    """
    messages = [{"role": "system", "content": build_system_prompt(session_key)}]
    if use_history:
        for role, past in _history.get(session_key):
            messages.append({"role": role, "content": past})
    messages.append({"role": "user", "content": content})
    return messages


def _is_inspection(body: str) -> bool:
    """响应体是不是「内容审核拦截」。

    ⚠️ 必须和普通 400（参数写错之类）**分开**：前者能靠丢历史自愈，
    后者重试多少次都一样。判据用 ``data_inspection_failed`` 这个 code。
    """
    return "data_inspection_failed" in body


async def _post(settings: _Settings, payload: dict) -> tuple[dict | None, str]:
    """发一次请求。

    :returns: ``(响应体, 状态)``。状态是 ``"ok"`` / ``"inspection"`` / ``"error"``。
        ``inspection`` 的含义与处理见 :func:`reply` 的自愈逻辑。
    """
    try:
        async with _SEM:
            # 每次新建 session（省掉生命周期管理）。几十条/天的量，
            # 多一次 TCP+TLS 握手完全无所谓；将来上量了再改成复用。
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as session:
                async with session.post(
                    f"{settings.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _log.warning(
                            "AI 调用失败：HTTP %s %s", resp.status, body[:200]
                        )
                        return None, (
                            "inspection" if _is_inspection(body) else "error"
                        )
                    return await resp.json(), "ok"
    except Exception:
        # 刻意吞掉一切 —— AI 挂了不该影响机器人的其它功能
        _log.exception("AI 调用异常")
        return None, "error"


async def reply(text: str, session_key: str | None = None) -> str | None:
    """把一条群消息交给模型，返回过滤后的回复。

    **任何失败都返回 ``None``**（未配置 / 超额 / 网络异常 / 非 200 / 结构异常 /
    过滤后为空），由调用方决定怎么降级 —— 本函数从不抛异常。

    流程（顺序有讲究）：

    0. **主动问好感度** ⇒ 直接回数值，**不调 API**。既免费，又保证数值准确
       （让模型转述数字迟早会说错）。
    1. **预算闸门** —— 超额直接返回 ``None``（静默降级，与其它失败一致）。
    2. 组装 messages（人设 + 窗口 + 本条）。
    3. 调用。**被内容审核拦下时丢历史重试一次**（见下）；重试仍被拦 ⇒
       回 ``ai.reply_blocked``（固定推托），**不是** ``None``。
    4. 成功 ⇒ **成对**记进窗口 + 调好感度。失败则窗口不动 ——
       只记 user 不记 assistant 会让下一轮模型以为它没回过，重复作答。

    ⚠️ **为什么要有第 3 步的自愈**：百炼对**整条输入**做内容审核，
    命中就返回 400 ``data_inspection_failed``。而窗口是**粘性**的 ——
    那条被判定不合适的历史**会一直跟着**，导致这个会话之后每次请求都 400，
    **不会自愈**，AI 从此不吭声（2026-09-28 日志实测：连续两次 400 后就没反应了）。
    ⇒ 丢掉该会话窗口 + 只用「人设 + 本条」重试一次。

    ⚠️ **为什么重试失败还要回一句推托、而不是静默**：合规拦截是**用户视角的
    「机器人坏了」** —— 他 @ 了，什么都没等到。回一句固定的、安全的推托，
    至少让「沉默」变成「Liz 明确不接这个话题」。这也是「内容合规兜底」的一层，
    另外两层在 :func:`liz_bot.qqgroupbot` 的发送侧（见 ``_send_safe``）。

    :param text: 消息正文（@ 部分由调用方先去掉）。
    :param session_key: 会话键（``群:成员``）。**不给就没有上下文、也没有好感度** ——
        拿不到会话键时（见 ``qqgroupbot._session_key``）单轮回答，行为与从前一致。
    :returns: 可直接发进群的文本；不可用时为 ``None``。
    """
    settings = _settings_or_none()
    if settings is None:
        return None

    content = (text or "").strip()
    if not content:
        return None

    # 0) 主动询问好感度 —— 走本地数值，不烧 token
    if session_key and affinity.is_inquiry(content):
        value = affinity.load(session_key)
        labels = replies.get("ai.affinity_labels")
        return replies.text(
            "ai.affinity_ask", value=value, label=labels[affinity.band(value)]
        )

    # 1) 预算闸门。**放在 API 调用之前**，扣的是「预留名额」——
    #    并发时两个协程不会都读到「还剩 1 次」然后各调一次。
    if not BUDGET.allow(session_key):
        used, total = BUDGET.snapshot()
        _log.warning("AI 今日额度已用尽（%s/%s），本条消息静默降级", used, total)
        return None

    messages = _build_messages(content, session_key)
    if _prompt_chars(messages) > MAX_PROMPT_CHARS:
        # 先丢历史 —— 多数「太长」是窗口顶上去的，「人设 + 本条」往往就装得下。
        _log.warning(
            "AI 输入超长（%s > %s 字符），丢掉历史后重试",
            _prompt_chars(messages),
            MAX_PROMPT_CHARS,
        )
        messages = _build_messages(content, session_key, use_history=False)

    if _prompt_chars(messages) > MAX_PROMPT_CHARS:
        # 连「人设 + 本条」都装不下 ⇒ 是**这条消息本身**太长。
        # ⚠️ 不能返回 None：那又是一次「@ 了没反应」，和合规拦截是同一类毛病。
        _log.warning("本条消息本身超长（%s 字符），回固定推托", len(content))
        return replies.text("ai.reply_too_long")

    payload = {
        "model": settings.model,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        # ⚠️ 必须显式关掉 —— 默认是**开**的，见 _THINKING_NOTE。
        "enable_thinking": False,
    }

    data, kind = await _post(settings, payload)

    # ⚠️ 内容审核拦了**整条输入** ⇒ 历史里可能有一条被判定为不合适的内容，
    #    而它**会一直留在窗口里** ⇒ 这个会话之后每次请求都会带着它一起 400，
    #    **而且不会自愈**，AI 从此不吭声。
    #    （2026-09-28 日志实测 botpy.log:957 —— 连续两次 400 之后就没反应了。）
    # ⇒ 丢掉该会话的窗口，用「人设 + 本条」重试一次。
    if kind == "inspection" and session_key:
        _log.warning(
            "输入被内容审核拦截（本条 %s 字），丢弃该会话窗口后重试一次",
            len(content),
        )
        _history.drop(session_key)
        data, kind = await _post(
            settings,
            {**payload, "messages": _build_messages(content, session_key, use_history=False)},
        )

    # ⚠️ 丢历史 + 重试**还是**被拦 ⇒ 触发审核的就是**本条消息本身**。
    #    这时**不能返回 None**：用户 @ 了机器人却一个字都收不到，
    #    只会以为「机器人又崩了」（2026-09-28 实测就是这个现象）。
    # ⇒ 回一句**固定的、安全的**推托。它不含任何可能被拒的内容，所以一定发得出去；
    #    也**不进窗口、不动好感度** —— 那句话本来就不该被记住。
    if kind == "inspection":
        _log.warning("本条消息本身触发内容审核，回固定推托（不回显原文）")
        return replies.text("ai.reply_blocked")

    if kind != "ok" or data is None:
        return None

    answer = _extract(data, session_key)
    if not answer:
        return None

    answer = sanitize(answer)
    if not answer:
        # 整条回复都是链接（模型偶尔会这样），过滤完就空了 —— 不发空消息
        _log.info("AI 回复被 URL 过滤后为空，已丢弃")
        return None

    # 4) 成对入窗口 + 好感度。**只在真正回了话之后**做 ——
    #    失败的消息不进上下文，否则模型会「记住」一句它没答过的话。
    if session_key:
        _history.append(session_key, "user", content)
        _history.append(session_key, "assistant", answer)
        affinity.adjust(session_key, affinity.delta_for(content))
    return answer


#: 主动开口时附加的**临时指令**（2026-09-29）。
#:
#: ⚠️ 刻意短、刻意**不带示例**：一给示例，模型就会把示例的句式学进每一条回复
#: （本项目在「示例权重压倒规则」上栽过两次，见 :data:`BUBBLE_SEPARATOR` 的注释）。
#: ⚠️ 最后一句「别提这段说明」是防泄漏 —— 不加的话模型偶尔会回一句
#: 「（我主动说点什么）」之类把指令本身复述出来。
_PROACTIVE_HINT = (
    "现在没有人在跟你说话，是你在群里自己想开口。"
    "随便说一句轻松的话，十来个字，别提问、别招呼谁，也别提这段说明。"
)


async def proactive_line() -> str | None:
    """生成一句 Liz **自己开口**的话 —— 群聊主动推送用（2026-09-29）。

    与 :func:`reply` 的三点不同：

    1. **不带任何用户消息** —— 只有人设 + :data:`_PROACTIVE_HINT`；
    2. **不写窗口、不动好感度** —— 它不是对谁的回复，塞进滑动窗口只会污染上下文
       （下一次有人 @ 时模型会以为它已经跟人聊过一轮）；
    3. 会话键固定成 ``__proactive__``，只用来走 :data:`BUDGET` 的额度。

    ⚠️ **仍然要过预算闸门**：定时任务最容易悄悄把当日额度吃光，
       而 :data:`BUDGET` 是全局的 —— 吃完别人当天就没得用了。

    任何失败返回 ``None``（未配置 / 超额 / 网络异常 / 结构异常 / 过滤后为空），
    调用方据此**跳过本次推送**，绝不发半截东西。本函数从不抛异常。
    """
    settings = _settings_or_none()
    if settings is None:
        return None

    if not BUDGET.allow(_PROACTIVE_BUDGET_KEY):
        used, total = BUDGET.snapshot()
        _log.warning("主动推送：AI 今日额度已用尽（%s/%s），跳过", used, total)
        return None

    payload = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": replies.text("ai.system_prompt")},
            {"role": "user", "content": _PROACTIVE_HINT},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        # ⚠️ 同 :func:`reply` —— 不显式关掉就是白烧 82 倍输出 token。
        "enable_thinking": False,
    }

    data, kind = await _post(settings, payload)
    if kind != "ok" or data is None:
        _log.warning("主动推送：生成失败（%s）", kind)
        return None

    answer = sanitize(_extract(data, _PROACTIVE_BUDGET_KEY))
    if not answer:
        _log.info("主动推送：生成结果为空或全是链接，跳过")
        return None
    return answer

