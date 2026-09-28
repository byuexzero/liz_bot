"""群聊 AI 回复 —— Qwen3.7-Flash（最小可用版）。

只做三件事：

1. **接一条消息、回一句话** —— 调用方（``qqgroupbot``）已经把范围限死在
   「群 @ 消息」上，所以这里不再判断触发条件。
2. **调一次百炼** —— 走 OpenAI 兼容接口，用 ``aiohttp`` 直发。
3. **强制 URL 过滤** —— QQ 开放平台对含 URL 的消息**直接拒发**（错误码
   ``40054010``），而模型特别爱输出链接。所以过滤必须落在代码里，
   **不能指望提示词管住它**：提示词是建议，这里是保证。

设计原则
--------
* **失败一律静默降级**：任何异常都吞掉、返回 ``None``，让调用方走原来的逻辑。
  AI 是锦上添花，不该因为它挂了就让人连 ``/help`` 都用不了。
* **不加新依赖**：用项目已有的 ``aiohttp``。引 ``openai`` SDK 会让
  ``requirements.txt`` 变化 ⇒ Docker 的 ``COPY requirements.txt`` 层缓存失效
  ⇒ 部署要重跑 apt + pip。
* **强制关闭思考模式**：见 :data:`_THINKING_NOTE`。
* **不带历史**：最小版本每条消息独立调用。多轮上下文是下一步的事
  （见 ``Qwen3.7Flash可行性调研.md`` §6）。
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import NamedTuple

import aiohttp
from botpy import logging

from liz_bot import replies

# ⚠️ 这一行**只为副作用**：``liz_bot.config`` 在模块级把项目根的 ``.env``
# 灌进 ``os.environ``（容器里没有该文件，是 no-op）。
# 少了它，单独跑本模块（比如测试脚本）时 ``LIZ_AI_API_KEY`` 不会进环境变量，
# ``available()`` 会莫名其妙返回 False。
from liz_bot import config as _config  # noqa: F401  (仅为触发 .env 加载)

#: 指令前缀。**从 ``command_router`` 取，不在这里硬编码** ——
#: 触发判断是「AI 会不会抢走指令」的唯一防线，前缀写错就是群里指令失灵。
#: （``command_router`` 不反向 import 本模块，无循环。）
from liz_bot.command_router import PREFIX_MAIMAI, PREFIX_NORMAL

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
#: 烧掉 ``400 × 0.8 / 1e6 ≈ 0.0003`` 元。
MAX_TOKENS = 400

#: 单次请求超时（秒）。
#:
#: 被动消息的有效期是 5 分钟，给足余量但仍然要有上限 —— 否则一次卡死
#: 会一直占着 :data:`MAX_CONCURRENCY` 的名额。
REQUEST_TIMEOUT = 45.0

#: 同时进行的 AI 请求上限。防止有人刷屏时把并发打满。
MAX_CONCURRENCY = 3

_SEM = asyncio.Semaphore(MAX_CONCURRENCY)

#: 系统提示。
#:
#: 刻意写得短 —— 它**每次请求都要重发**，直接计入输入 token。
#: 最后一条（不要输出链接）是提示词层面的尽力而为，真正的保证在
#: :func:`sanitize`。
SYSTEM_PROMPT = (
    "你是 QQ 群里的聊天机器人，名字叫 Liz。群里的人在 @ 你聊天。\n"
    "要求：\n"
    "- 用中文，口语化，像群友一样自然，别端着\n"
    "- 简短。一两句就够，最多三句 —— 群里没人想读长文\n"
    "- 不要输出任何网址或链接\n"
    "- 不要用 markdown（QQ 不渲染），不要用星号加粗\n"
    "- 不知道就说不知道，别编"
)

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


def should_handle(content: str) -> bool:
    """该不该把这条群消息交给 AI。

    = 已配置 key（:func:`available`）**且**不是指令。

    抽成函数是为了**可测**：这是本轮唯一「写反了也不报错、只在群里表现异常」
    的判断 —— 判断过松会让 AI 抢走 ``/估分`` 之类的指令，过严则 @ 了没反应。
    调用方（``qqgroupbot``）只负责把范围限死在「群 @ 消息」上。

    :param content: 消息正文。
    """
    return available() and not is_command(content)


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


async def reply(text: str) -> str | None:
    """把一条群消息交给模型，返回过滤后的回复。

    **任何失败都返回 ``None``**（未配置 / 网络异常 / 非 200 / 结构异常 /
    过滤后为空），由调用方决定怎么降级 —— 本函数从不抛异常。

    :param text: 消息正文（@ 部分由调用方先去掉）。
    :returns: 可直接发进群的文本；不可用时为 ``None``。
    """
    settings = _settings_or_none()
    if settings is None:
        return None

    content = (text or "").strip()
    if not content:
        return None

    payload = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "max_tokens": MAX_TOKENS,
        # ⚠️ 必须显式关掉 —— 默认是**开**的，见 _THINKING_NOTE。
        "enable_thinking": False,
    }

    try:
        async with _SEM:
            # 最小版本每次新建 session（省掉生命周期管理）。30 条/天的量，
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
                        return None
                    data = await resp.json()
    except Exception:
        # 刻意吞掉一切 —— AI 挂了不该影响机器人的其它功能
        _log.exception("AI 调用异常")
        return None

    answer = _extract(data)
    if not answer:
        return None

    answer = sanitize(answer)
    if not answer:
        # 整条回复都是链接（模型偶尔会这样），过滤完就空了 —— 不发空消息
        _log.info("AI 回复被 URL 过滤后为空，已丢弃")
        return None
    return answer
