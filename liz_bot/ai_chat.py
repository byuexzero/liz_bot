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

from liz_bot import affinity, replies
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


def should_handle(content: str, session_key: str | None = None) -> bool:
    """该不该把这条群消息交给 AI。

    三条同时成立才接管：

    1. 已配置 key（:func:`available`）；
    2. **不是指令**（:func:`is_command`）；
    3. **该会话没有正在等的补参 / 选候选**（``command_router.has_pending``）。

    第 3 条是 2026-09-28 用户实测出来的：AI 分支跑在 ``reply_text`` **之前**，
    而补参状态是在 ``reply_text`` 里消费的 —— 少了它，
    「还差 1 个参数（难度）～」之后用户回「紫」，会被 AI 当成闲聊回一句，
    补参永远凑不齐（消歧时回序号 ``1`` 也一样被抢）。等待状态有 60 秒 TTL
    （见 ``liz_bot.pending``），所以 AI 最多让路 60 秒，之后照常接管。

    抽成函数是为了**可测**：这是唯一「写反了也不报错、只在群里表现异常」
    的判断 —— 过松会让 AI 抢走指令或补参，过严则 @ 了没反应。
    调用方（``qqgroupbot``）只负责把范围限死在「群 @ 消息」上。

    :param content: 消息正文。
    :param session_key: 会话键（``群:成员``）。**不给就当作没有等待状态** ——
        拿不到会话键时补参本来也不工作（见 ``qqgroupbot._session_key``）。
    """
    return available() and not is_command(content) and not has_pending(session_key)


# ---------------------------------------------------------------------------
# URL 过滤
# ---------------------------------------------------------------------------
#: URL 的**终止字符** —— 空白 + 常见中文标点。
#:
#: ⚠️ 不能只用 ``\S+``：中文标点不是空白，``见 https://a.com，然后`` 会把
#: 「，然后」一起吞进 URL，替换后句子就残了。
_URL_STOP = r"\s，。！？、；：“”‘’（）【】《》…—～·"

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


def _extract(data: dict) -> str:
    """从响应体里取出回复文本，并记一行 token 用量。

    记用量是为了**能盯成本** —— 万一哪天忘了关思考模式，日志里
    ``思维链`` 那个数会立刻涨到几百，一眼就能看出来。
    """
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        _log.warning("AI 返回结构异常：%s", str(data)[:200])
        return ""

    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    _log.info(
        "AI 用量：输入 %s / 输出 %s token（其中思维链 %s）",
        usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
        details.get("reasoning_tokens", 0),
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

    answer = _extract(data)
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
