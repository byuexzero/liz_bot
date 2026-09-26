"""指令入口层 —— 解析、校验与分发。

两种指令前缀
------------
=================  ============================================================
前缀               去向
=================  ============================================================
``/``             本机指令（查歌 / 随机数 / 帮助……），见 :data:`COMMANDS`
``#``             舞萌命名空间 —— **实现尚未接入**，统一回复「未解析的指令」
=================  ============================================================

:func:`reply_text` 是 **bot 外壳的唯一入口**：识别前缀 → 解析 → 分发，把一条
消息变成一段回复文本。``qqgroupbot`` 只负责收发，不碰解析逻辑。

指令名**大小写不敏感**（``/HELP`` 与 ``/help`` 等价，见 :func:`_build_index`）。

多轮补参
--------
参数不够时**不直接报错**，而是记下「这个会话在补哪条指令」，等用户的下一条
消息当参数（见 :mod:`liz_bot.pending`）。缺多个参数可以：

* **一条消息按顺序补齐** —— ``/添加别名`` → 追问 → ``8 测试别名``；
* 也可以**分多次补**，参数按顺序**叠加**。

追问文案**不带指令头**（只说「还差几个、还差哪几个参数」，见 :func:`_ask_text`），
参数名从 help 文案里的 ``<...>`` 占位符派生，所以只有一份、不会漂移。

同名消歧
--------
同名不同版本（**SD / DX**）在曲库里是**两条记录**，所以按歌名或别名查会命中
多首。此时**不再静默取第一条**，而是列出候选、等用户回一句序号或 id::

    /bm ジングルベル     ← 这个别名同时挂在 SD(70) 与 DX(10070) 上
    Liz : 匹配到 2 首，回复序号或 id 选择：
            1. SD ジングルベル id 70
            2. DX ジングルベル id 10070
    2                    ← 选 DX
    Liz : <DX 那首的卡片>

消歧**复用补参的同一套会话状态**（:class:`liz_bot.pending.Pending` 的
``choices`` 字段），因此 TTL、会话隔离、「指令优先」「超时作废」全都自动适用，
见 :func:`_resume_choice`。

两种等待都**需要调用方给 ``session_key``** 才启用；不给就是无状态的一次性分发
（命中多个候选时退回取首条 —— 重构前的行为）。

本模块是**指令的唯一事实来源**：

* :data:`COMMANDS` —— 一张表声明「有哪些指令、各自叫什么名字和别名、
  收几个参数、在 ``/help`` 里怎么显示」；
* :data:`_HANDLERS` —— 声明「怎么处理」。

两者的键**在 import 时校验必须一一对应**：漏一个立刻抛 ``RuntimeError``，
而不是等群里某条消息才暴露。加指令只需动这两个地方 + 一条 help 文案。

不含任何业务逻辑，具体功能实现见：
    song_query.py      查歌 + 别名查询
    judge_detail.py    谱面判定细节（``/songdata``）—— 物量 + 各判定档位的扣分
    score_estimate.py  估分（``/估分``）—— 目标达成率 → 可上传的判定分布
    song_alias.py      别名管理 —— **写入已停用，仅保留接口**
    daily_funcs.py     随机数 / 问候

注：``/qr``（扫码查询）已**暂时移除**——它是对舞萌服务端的发包功能。
它依赖的服务端 API 工具链**不随仓库分发**（见 ``.gitignore``）——
那是一个功能完整的上传工具，公开分发不合适；需要时从本地获取。

注：``/bm``、``/别名查歌``、``/查询别名``、``/cbm`` 现走**柚子（yuzuchan）**
别名库，数据见 ``liz_bot/yuzuchan_aliases/aliases.json``（由
``_tools/fetch_aliases.py`` 拉取）。

注：``/添加别名`` 仍**已停用** —— 别名库是只读快照，下次拉取就会覆盖；
上游是玩家众包投票制，本地写会与之分叉。

注：本模块**不再硬编码任何用户可见文案**（「未知指令」等），全部来自
``liz_bot/texts/replies.json``，见 :mod:`liz_bot.replies`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from liz_bot import (
    daily_funcs, estimate_image, judge_detail, judge_image, pending, replies,
    score_estimate, song_alias, song_query,
)

# ---------------------------------------------------------------------------
# 指令表 —— 唯一事实来源
# ---------------------------------------------------------------------------

#: ``/help`` 里别名之间的分隔符（纯排版，不是文案）
_ALIAS_SEP = " / "


@dataclass(frozen=True)
class RichReply:
    """一条**富媒体**回复：能发图就发图，发不出去就用 ``fallback`` 文本。

    为什么不直接让处理函数返回它：处理函数的签名是
    ``(params, miss, session_key) -> str``，改签名会波及 ``song_query`` /
    ``judge_detail`` 的公开函数（自检脚本与等价性测试都在直接调它们）。
    所以图片渲染放在**分发层**（见 :func:`_render_rich`），处理函数照旧只产文本
    —— 那份文本顺便就成了天然的降级内容，不用另写一份。

    :param png: PNG 字节
    :param filename: 上传时告诉服务端的文件名（只影响它那边的记录）
    :param fallback: 发图失败时改发的文本（**就是同一份文字版回复**）
    """

    png: bytes
    filename: str
    fallback: str


@dataclass(frozen=True)
class Command:
    """一条指令的元数据。

    :param key: ASCII 标识。help 文案键为 ``commands.<key>``
        （键名用 ASCII 是为了让 ``replies.json`` 里的路径好读、好引用）
    :param names: 可识别的名字，**第 0 个是规范名**（其余为别名）。
        规范名必须与 ``commands.<key>`` 文案的开头一致（``help_reply`` 会断言）。
    :param min_params: 最少参数个数
    :param max_params: 最多参数个数；``None`` = 不限
        （含空格的曲名/别名会被切成多个 token，所以这类指令不能设上限）
    :param listed: 是否在 ``/help`` 里列出。停用的指令仍可解析，
        只是不出现在帮助里 —— 否则等于教用户去用一个坏掉的功能。
    """

    key: str
    names: tuple[str, ...]
    min_params: int
    max_params: int | None
    listed: bool = True


#: 全部指令。**加指令改这里 + :data:`_HANDLERS` + ``replies.json`` 一条文案。**
COMMANDS: tuple[Command, ...] = (
    Command("help", ("help",), 0, 0),
    Command("hello", ("hello",), 0, 0),
    Command("random", ("random",), 0, None),
    # id 的参数是单个数字，多给就是写错了 —— 设上限，明确报错
    Command("id", ("id", "id查歌", "songid"), 1, 1),
    # songdata 要「歌曲id + 难度」两个参数：只给 id 时走多轮补参追问难度
    Command("songdata", ("songdata",), 2, 2),
    # 估分：「歌曲id + 难度 + 百分比」必填，「星级」可选（省略按 0，即不限星级）。
    # 四个参数都是单个 token，所以设了上限。
    Command("estimate", ("估分", "estimate"), 3, 5),
    # 以下四类的关键词都可能含空格（曲名 / 别名），故不设参数上限
    Command("bm", ("bm", "别名查歌", "songalias"), 1, None),
    Command("name", ("name", "歌名查歌", "songname"), 1, None),
    Command("song", ("song",), 1, None),
    Command("alias_query", ("查询别名", "cbm"), 1, None),
    # 写入已停用，故不在 /help 里列出
    Command("alias_add", ("添加别名", "addalias"), 2, 2, listed=False),
)


def _build_index() -> dict[str, Command]:
    """把 ``COMMANDS`` 摊平成「**小写**名字/别名 → 指令」的字典。

    * 键统一转小写 —— 指令名**大小写不敏感**（``/HELP`` == ``/help``）。
      中文名不受影响：``str.lower()`` 对汉字是恒等变换，所以
      ``/别名查歌`` 照旧可用。
    * 指令名重复会让「同名不同指令」静默地按声明顺序胜出，是最难查的一类 bug，
      所以在 import 时直接拒绝。判重也用**小写形式**，这样 ``Song`` / ``song``
      这种「只差大小写」的重复同样会被拦下。

    .. note::

       小写化只发生在**查表**时，不改 ``parse_command`` 的返回值 ——
       于是「未知指令」的提示能原样回显用户输入的写法（``未知指令：HELP``），
       而不是被悄悄改写成小写。
    """
    index: dict[str, Command] = {}
    for entry in COMMANDS:
        for name in entry.names:
            key = name.lower()
            if key in index:
                raise RuntimeError(
                    f"指令名重复：{name!r} 同时属于 "
                    f"{index[key].key!r} 与 {entry.key!r}"
                    f"（判重不区分大小写）"
                )
            index[key] = entry
    return index


_BY_NAME: dict[str, Command] = _build_index()

#: ``key`` → :class:`Command`。多轮补参的状态里存的是 key，靠它还原成指令。
#:
#: 建表前先查重复：重复的 key 会让这个字典**静默丢掉一条**，而下面那句
#: ``set(_HANDLERS) == {e.key for e in COMMANDS}`` 发现不了 —— 集合会自动去重。
_KEYS = [entry.key for entry in COMMANDS]
if len(set(_KEYS)) != len(_KEYS):
    _dup_keys = sorted({k for k in _KEYS if _KEYS.count(k) > 1})
    raise RuntimeError(f"COMMANDS 里有重复的 key：{_dup_keys}")

_COMMANDS_BY_KEY: dict[str, Command] = {e.key: e for e in COMMANDS}


def _lookup(cmd_name) -> Command | None:
    """按指令名（规范名或别名）查表。

    **大小写不敏感**：查表前统一转小写，但调用方拿到的仍是原始 ``cmd_name``，
    所以「未知指令」的提示能回显用户原本的写法。
    非字符串（调用方误传 ``None``）按查不到处理。
    """
    key = cmd_name.lower() if isinstance(cmd_name, str) else ""
    return _BY_NAME.get(key)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

#: 本机指令前缀（查歌 / 随机数 / 帮助……）。
PREFIX_NORMAL = "/"

#: 舞萌指令前缀。**实现尚未接入** —— 见 :func:`handle_maimai_command`。
PREFIX_MAIMAI = "#"

#: 指令正文：``指令名`` + 可选参数。两种前缀共用同一套语法。
#:
#: ``re.DOTALL`` 是**必须的**：默认 ``.`` 不匹配换行，于是
#: ``/song 8\n随便说点什么`` 整条都匹配不上，直接落到「未知指令」。
#: 而 QQ 群里多行输入很常见（尤其是手机端换行）。
#: 开 DOTALL 后 ``(.*)`` 能跨行，参数由 ``str.split()`` 切成 token，
#: 换行与空格一样被当作分隔符 —— 与单行行为完全一致。
_BODY = r"(\w+)(?:\s+(.*))?$"

#: ``/`` 开头的本机指令。
#:
#: ⚠️ 这个正则**必须与重构前归档件逐字节一致** ——
#: ``_tools/test_refactor_equivalence.py`` 会逐条比对 ``parse_command`` 的
#: 返回值，所以这里不做任何「顺手」的增强（大小写处理放在**查表**侧，
#: 见 :func:`_build_index`）。
_COMMAND_PATTERN = re.compile(r"^/" + _BODY, re.DOTALL)

#: ``#`` 开头的舞萌指令 —— 语法与本机指令完全相同，只是前缀不同。
_MAIMAI_PATTERN = re.compile(r"^" + re.escape(PREFIX_MAIMAI) + _BODY, re.DOTALL)


async def parse_command(message_content: str):
    """解析指令：``/指令名 参数1 参数2`` → ``(指令名, [参数1, 参数2], True)``

    无参数时返回 ``(指令名, [], True)``，非指令格式返回 ``(None, None, False)``。

    ⚠️ ``\\w`` 在 Python 的 ``str`` 模式下是 Unicode 语义，所以**中文指令名**
    （``/别名查歌``）可用；但它不含 ``-``、``.``，所以 ``/song-name`` 解析不出来。

    ⚠️ 参数**只按空白切分**（``str.split()``，含全角空格与换行），
    不处理引号 —— ``/song "true love song"`` 会切成 ``['"true', 'love', 'song"']``。
    含空格的曲名/别名靠 :func:`_keyword_variants` 重新拼回整串来救。
    """
    content = message_content.strip()
    match = _COMMAND_PATTERN.match(content)

    if not match:
        return None, None, False

    cmd_name = match.group(1)
    params_str = match.group(2)  # 所有参数的原始字符串（可含换行）
    cmd_params = params_str.split() if params_str else []

    return cmd_name, cmd_params, True


async def parse_maimai_command(message_content: str):
    """解析 ``#`` 开头的舞萌指令 → ``(指令名, [参数...], True)``。

    语法与 :func:`parse_command` **完全一致**（共用同一个 ``_BODY``），只是前缀
    从 ``/`` 换成 ``#``：``#b50``、``#上传 123``。因此 ``# song 8``（``#`` 后带
    空格）同样解析失败 —— 两种前缀的规则保持一致，不让人猜。

    ⚠️ 舞萌指令**实现尚未接入**（见 :func:`handle_maimai_command`）。
    这里先把解析写出来，是为了让「加一条舞萌指令」和「加一条本机指令」的路径
    一致：将来只要补一张表 + 一个处理函数，:func:`reply_text` 的分发不必再动。

    :return: ``(指令名, 参数列表, True)``；不是 ``#`` 指令格式则
        ``(None, None, False)``
    """
    content = message_content.strip()
    match = _MAIMAI_PATTERN.match(content)

    if not match:
        return None, None, False

    cmd_name = match.group(1)
    params_str = match.group(2)  # 所有参数的原始字符串（可含换行）
    cmd_params = params_str.split() if params_str else []

    return cmd_name, cmd_params, True


def is_maimai_command(message_content: str) -> bool:
    """消息是否落在 ``#`` 命名空间里。

    判据只看**前缀**，不要求能解析成功：舞萌指令当前一律回复「未解析的指令」，
    所以 ``#`` 后面写了什么（哪怕格式不对）都该得到同一句话，而不是掉进
    「未知指令」让人误以为 ``#`` 这个前缀不被认识。
    """
    return message_content.strip().startswith(PREFIX_MAIMAI)


def _keyword_variants(cmd_params):
    """关键词候选，按「完整参数串 → 首 token」的顺序。

    为什么不能只取首 token
    ----------------------
    ``parse_command`` 是按空白切分的，只取 ``cmd_params[0]`` 会让**含空格的
    关键词永远查不到**。而这类关键词很多：实测柚子别名库 9853 条别名里有
    **672 条（6.8%）含空格**（``true love song``、``color my world`` …），
    曲名同理（``True Love Song``、``7thSense`` 之外的一堆英文名）。

    先试整串、再退回首 token，因此是旧行为的**超集**：
    ``/song 8 extra`` 这类脏输入仍能退回首 token 命中 id 8。

    ⚠️ 代价是**静默降级** —— 用户不知道命中的是整串还是首 token。
    """
    if not cmd_params:
        return []
    joined = " ".join(cmd_params).strip()
    if joined and joined != cmd_params[0]:
        return [joined, cmd_params[0]]
    return [cmd_params[0]]


def _render_hit(entry_key: str, hit: dict) -> str:
    """把一条命中渲染成回复 —— **渲染方式由指令决定**。

    ==================  ==============================================
    指令 key            渲染成什么
    ==================  ==============================================
    ``alias_query``    别名列表（``/查询别名``）
    其余查歌指令        单曲卡片（``/bm`` / ``/name`` / ``/song``）
    ==================  ==============================================

    消歧选中之后也要走这里，所以「同一份命中在不同指令下渲染不同」
    必须收在一个函数里，不能散在两处。
    """
    if entry_key == "alias_query":
        return song_query.alias_text(hit)
    return song_query.format_song(hit["song"])


def _choose_prompt(
    session_key: str | None, entry_key: str, keyword: str, hits: list[dict]
) -> str:
    """命中多个候选 —— 记下选择状态并追问「选哪一个」。

    ``session_key`` 为空（无状态调用：自检脚本、等价性测试）时退回**第一条**
    命中，也就是重构前的行为 —— 这样 ``handle_command`` 的直接调用方不变。

    候选 id 转不出 int 时同样退回首条：否则序号会与实际候选错位，
    用户按序号选到的将是另一首。
    """
    ids = [song_query.song_id(h) for h in hits]
    if not session_key or any(i < 0 for i in ids):
        return _render_hit(entry_key, hits[0])

    # params 里存触发本次选择的关键词，只作调试线索（见 pending.Pending.params）
    _PENDING.put(session_key, entry_key, [keyword], choices=ids)
    return song_query.choose_text(hits)


def _reply_variants(cmd_params, entry_key, mode, session_key, miss):
    """按关键词候选依次检索，命中即渲染；全部未命中返回 ``miss``。

    与旧版的区别：旧版是「调用 ``reply_fn``，看结果是否等于 ``miss`` 哨兵」，
    新版直接看 :func:`song_query.select_song` 的**命中条数** —— 因为
    「命中了几首」正是消歧要用的信息，从渲染好的字符串里读不出来。

    命中多个候选时**不再静默取第一条**，改为让用户选（见 :func:`_choose_prompt`）。
    """
    for keyword in _keyword_variants(cmd_params):
        hits = song_query.select_song(keyword, mode)
        if not hits:
            continue
        if len(hits) == 1:
            return _render_hit(entry_key, hits[0])
        return _choose_prompt(session_key, entry_key, keyword, hits)
    return miss


# ---------------------------------------------------------------------------
# 处理函数
# ---------------------------------------------------------------------------

def _h_help(cmd_params, miss, session_key):
    return help_reply()


def _h_hello(cmd_params, miss, session_key):
    return daily_funcs.greeting_reply()


def _h_random(cmd_params, miss, session_key):
    return daily_funcs.random_int_from_list(cmd_params)


def _h_id(cmd_params, miss, session_key):
    # BY_ID 最多命中一首，不存在歧义 —— 不必走 _reply_variants
    return song_query.song_reply(cmd_params[0], song_query.BY_ID)


def _h_songdata(cmd_params, miss, session_key):
    return judge_detail.judge_detail_reply(cmd_params[0], cmd_params[1])


def _estimate_args(cmd_params) -> tuple[str, str, str, str, str, str]:
    """``/估分`` 的参数整理 → ``(歌曲id, 难度, 百分比, 星级, combo, x小)``。

    三个位置都能省，而且**百分比也能省**（AP / AP+ 根本用不上它）::

        /估分 147 紫 100.0 2 FC     # 全给
        /估分 147 紫 100.0 2        # 不要 combo
        /估分 147 紫 100.0 FC       # 不要星级（FC 不可能是星级，无歧义）
        /估分 147 紫 AP             # 不要百分比（AP 不可能是百分比，无歧义）
        /估分 147 紫 AP 3           # 不要百分比，要 3★
        /估分 147 紫 dx理论          # 要 DX 满分（星级槽的另一种写法）
        /估分 147 紫 3小            # x小：break 的 3 颗小P（星级不限）
        /估分 147 紫 3小 4          # x小 定达成率，4★ 定 DX 分
        /估分 147 紫 100.0 3小 4    # 同上，百分比只是重复（会被忽略）

    ``x小`` / ``x小P`` **与位置无关**：它取代百分比，出现在哪一格都认得
    （见 :func:`liz_bot.score_estimate.parse_break_p`）。它把达成率与 combo
    钉死之后，剩下的格子按「两格 ⇒ ``百分比 星级``，一格 ⇒ 星级」读。

    其余参数走「**位置优先，只在无歧义时才顺移**」：某个位置放不下时才往后挪，
    绝不往回填。所以 ``/估分 147 紫 2 AP`` 里的 ``2`` 是**百分比** ——
    ``2`` 既是合法百分比也是合法星级，按位置走，**不猜**。

    整理不出来的值照样往下传，由 :func:`liz_bot.score_estimate.estimate`
    报出**具体**的错（``bad_percent`` / ``bad_stars`` / ``bad_combo``）。

    文字版与图片版共用这一份 —— 两边各写一遍必然漂移，而漂移的后果是
    「图里和文字里是不同的记录」。
    """
    rest = list(cmd_params[2:])
    percent = stars = combo = break_p = ""

    def is_combo(tok: str) -> bool:
        # 「不限」不是一档等级（没有 -1），所以这里只问「认不认得出来」
        return score_estimate.is_combo(tok)

    def is_star(tok: str) -> bool:
        """星级槽认得两种写法：星级本身，以及 ``dx理论``（DX 满分）。"""
        return (score_estimate.parse_dx_full(tok)
                or score_estimate.parse_stars(tok) is not None)

    # x小 / x小P 先摘出来：它出现在哪一格都算
    for i, tok in enumerate(rest):
        if score_estimate.parse_break_p(tok) is not None:
            break_p = rest.pop(i)
            break

    if break_p:
        # ``x小`` 已经把达成率与 combo 钉死，剩下的格子按「位置优先」读：
        #   * 两格 ⇒ ``百分比 星级``；
        #   * 一格 ⇒ 依次试 **combo → 星级 → 百分比**。``x小`` 之后百分比没有
        #     意义（只是重复一遍，``estimate()`` 会忽略并出一行说明），所以
        #     星级要排在它前面 —— ``/估分 147 紫 3小 4`` 的 ``4`` 是 4★，不是 4%。
        #     combo 排最前是为了让 ``estimate()`` 报出 combo_conflict 而不是
        #     把 ``FC`` 当成看不懂的星级。
        if len(rest) >= 2:
            percent = rest.pop(0)
        if rest:
            tok = rest.pop(0)
            if is_combo(tok) and not is_star(tok):
                combo = tok                       # 交给 estimate() 报 combo_conflict
            elif is_star(tok):
                stars = tok
            elif score_estimate.parse_percent(tok) is not None:
                percent = tok                     # 只是重复，estimate() 会忽略
            else:
                stars = tok                       # 交给 estimate() 报 bad_stars
        return cmd_params[0], cmd_params[1], percent, stars, combo, break_p

    if rest:
        tok = rest.pop(0)
        if score_estimate.parse_percent(tok) is not None:
            percent = tok
        elif is_combo(tok):
            combo = tok
        elif is_star(tok):
            stars = tok                           # 星级提前给，百分比留空
        else:
            percent = tok                         # 交给 estimate() 报 bad_percent
    if rest:
        tok = rest.pop(0)
        if is_star(tok):
            stars = tok
        elif not combo and is_combo(tok):
            combo = tok
        else:
            stars = tok                           # 交给 estimate() 报 bad_stars
    if rest:
        combo = rest.pop(0)                       # 只剩 combo 这一格

    return cmd_params[0], cmd_params[1], percent, stars, combo, break_p


def _h_estimate(cmd_params, miss, session_key):
    """``/估分 <歌曲id> <难度> <百分比> [星级] [combo]``。

    参数整理见 :func:`_estimate_args`。``session_key`` 透传下去，估分结果会
    按会话存进一轮缓存（见 :data:`liz_bot.score_estimate.CACHE`）。
    """
    song_id, difficulty, percent, stars, combo, break_p = _estimate_args(cmd_params)
    return score_estimate.estimate_reply(
        song_id, difficulty, percent, stars, combo, break_p, session_key=session_key,
    )


def _h_alias_query(cmd_params, miss, session_key):
    return _reply_variants(
        cmd_params, "alias_query", song_query.BY_ANY, session_key, miss
    )


def _h_alias_add(cmd_params, miss, session_key):
    return song_alias.add_alias_reply(cmd_params[0], cmd_params[1])


def _song_handler(cmd_key: str, mode: int):
    """按「查歌方式」造一个处理函数（``bm`` / ``name`` / ``song`` 共用）。

    :param cmd_key: 该指令在 ``COMMANDS`` 里的 key —— 渲染方式与消歧状态
        都靠它决定，所以显式传进来，而不是在函数里反查。
    :param mode: ``song_query.BY_ALIAS`` / ``BY_NAME`` / ``BY_ANY``
    """

    def handler(cmd_params, miss, session_key):
        return _reply_variants(cmd_params, cmd_key, mode, session_key, miss)

    return handler


#: 指令 key → 处理函数。签名统一为 ``(cmd_params, miss, session_key) -> str``。
#:
#: * ``miss`` 是「未命中」哨兵（见 :func:`handle_command`）；
#: * ``session_key`` 供**消歧**使用 —— 命中多个候选时靠它记住「等用户在选哪个」。
#:   多数处理函数用不上（``id`` / ``songdata`` 不可能命中多个），但签名保持统一，
#:   免得加一个新指令时又要动 :func:`handle_command` 的调用点。
_HANDLERS: dict[str, Callable[[list, str, "str | None"], str]] = {
    "help": _h_help,
    "hello": _h_hello,
    "random": _h_random,
    "id": _h_id,
    "songdata": _h_songdata,
    "estimate": _h_estimate,
    "bm": _song_handler("bm", song_query.BY_ALIAS),
    "name": _song_handler("name", song_query.BY_NAME),
    "song": _song_handler("song", song_query.BY_ANY),
    "alias_query": _h_alias_query,
    "alias_add": _h_alias_add,
}

# 表与处理函数必须严格一一对应 —— 漏一个就是「指令能解析但没人处理」，
# 会在群里表现成一句看不懂的 KeyError，故在 import 时直接拒绝。
if set(_HANDLERS) != {entry.key for entry in COMMANDS}:
    _only_table = sorted({e.key for e in COMMANDS} - set(_HANDLERS))
    _only_handler = sorted(set(_HANDLERS) - {e.key for e in COMMANDS})
    raise RuntimeError(
        "COMMANDS 与 _HANDLERS 不匹配："
        f"只在表里={_only_table}，只在处理函数里={_only_handler}"
    )


# ---------------------------------------------------------------------------
# 帮助
# ---------------------------------------------------------------------------

def help_reply() -> str:
    """``/help`` 的回复 —— 由 :data:`COMMANDS` 渲染出的指令列表。

    结构是「表头 + 每条指令一行 + 空行 + 舞萌命名空间说明」：
    末尾那段固定文案（``daily.help_maimai``）告诉用户 ``#`` 前缀的存在，
    否则 ``#b50`` 得到一句「未解析的指令」会让人莫名其妙。

    文案全部来自 ``replies.json``（``daily.help`` 表头 + 每条 ``commands.<key>``
    + ``daily.help_maimai`` 尾注），这里只负责拼接与**一致性断言**。

    **参数多的指令可以用简短写法**：若 ``commands.<key>_brief`` 存在就用它
    （只**缩短参数名**、不省略参数，免得整行在手机端折行）。
    ``commands.<key>`` 始终保留**完整签名** —— 补参追问要按它派生参数名
    （:func:`_param_names`），参数个数出错时也拿它当 usage。所以简短写法只影响
    ``/help`` 这一处，报错提示依旧是全的。

    ⚠️ 2026-09-26 用户要求：``/估分`` 的参数列表**必须列出 dx星级 与 combo**
    （此前收成 ``<多参数>``，用户看不出还能给什么）—— 简短写法只许缩短名字，
    不许把可选参数藏起来。

    ``/help`` 在 ``rich=True`` 时走图片版（见 :func:`_render_rich` 的 ``help``
    分支），这里返回的文字版始终是**兜底**。

    :raises RuntimeError: 某条指令的 help 文案与它的规范名对不上
        （典型场景：改了 ``COMMANDS`` 里的名字，忘了改 ``replies.json``）
    :raises RepliesError: ``replies.json`` 缺少对应键
    """
    lines: list[str] = []
    known = set(replies.keys())
    for entry in COMMANDS:
        if not entry.listed:
            continue

        # ``_brief`` 是**可选**的：有就用，没有就退回完整签名。
        # ⚠️ 不能用 ``replies.get`` 探路 —— 它缺键时是抛异常而不是返回 None。
        brief_key = f"commands.{entry.key}_brief"
        text = (replies.text(brief_key) if brief_key in known
                else replies.text(f"commands.{entry.key}"))
        expected = f"/{entry.names[0]}"
        if not text.startswith(expected):
            raise RuntimeError(
                f"指令表与 help 文案不一致：commands.{entry.key} = {text!r}，"
                f"应以 {expected!r} 开头（规范名取自 COMMANDS 的 names[0]）。"
                f"改指令名时请一并改 liz_bot/texts/replies.json。"
            )

        aliases = entry.names[1:]
        if aliases:
            text += replies.text("daily.help_alias", aliases=_ALIAS_SEP.join(aliases))
        lines.append(text)

    return (
        replies.text("daily.help")
        + "\n"
        + "\n".join(lines)
        + "\n\n"
        + replies.text("daily.help_maimai")
    )


# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------

#: 「参数值不对」类失败的文案键 —— 命中这些说明**参数值有问题**（而非个数不够），
#: 于是补参 / 消歧状态要**留住**让用户重发一次，而不是直接退出。
#:
#: ⚠️ **新增指令若引入了新的失败文案，必须登记到这里** —— 否则用户一旦输错
#:    就再也接不上话（只能把整条指令重打一遍）。这正是 2026-09-26 修的那个
#:    用户反馈的 bug 的成因。test_command_table.py 里有守卫：这里每个键
#:    都必须在 ``replies`` 的 ``_SCHEMA`` 里登记为字符串键（写错键名 / 忘了
#:    往 replies.json 加文案都会在那里报出来）。
_FAILURE_KEYS = (
    "song.not_found",          # 查歌：没这首歌
    "judge.bad_difficulty",    # /songdata：难度不认识
    "judge.no_chart",          # /songdata：这首歌没有该难度
    "router.bad_params",       # 参数太少（消歧路径把输入当关键词重跑时可能触发）
    "router.too_many_params",  # 参数太多（补参时一次给多了）
    # ---- /估分（见 liz_bot/score_estimate.py）----
    "estimate.bad_percent",        # 百分比看不懂
    "estimate.bad_stars",          # 星级看不懂
    "estimate.bad_combo",          # combo 等级看不懂
    "estimate.bad_break_p",        # x小 写法看不懂 / 超出 break 数
    "estimate.combo_conflict",     # x小 与别的 combo 等级冲突
    "estimate.dx_conflict",        # dx理论 与 x小 互斥（都钉死 DX 分）
    "estimate.need_percent",       # 没给百分比（只有 AP+ 可以不给）
    "estimate.ap_unreachable",     # AP 的达成率有下界，给低了取不到
    "estimate.too_high",           # 目标超过 101%
    "estimate.no_solution",        # 没找到可行分布
    "estimate.combo_unreachable",  # 该 combo 等级下取不到解
    "estimate.empty_chart",        # 谱面没有可判定的音符
)

#: 模板里的占位符，形如 ``{value}``。只用于**切分**失败文案（见 :func:`_failure_matcher`）。
_PLACEHOLDER = re.compile(r"\{[^{}]+\}")

#: 失败文案的「形状」缓存 —— **以模板原文为键**（不是键名）。
#:
#: 用模板原文做键是为了与 :mod:`liz_bot.replies` 的**热更新**对齐：改了
#: ``replies.json`` 里的文案，模板原文就变了，自然取到新编译的正则，
#: 绝不会用到旧的。旧条目留着也无害（键的种类很少，有天然上限）。
_FAILURE_MATCHERS: dict[str, "re.Pattern[str] | None"] = {}


def _failure_matcher(key: str) -> "re.Pattern[str] | None":
    """把一条失败文案编译成「匹配它渲染结果」的正则；不是模板则返回 ``None``。

    ⚠️ **绝不能**写成 ``text == replies.text(key)``：``judge.no_chart`` /
    ``router.bad_params`` 这些是**带占位符的模板**，凭空渲染会直接抛
    :class:`replies.RepliesError`（「是模板，必须提供占位符」）。
    2026-09-26 就这么炸过一次，而且因为 ``any()`` 只在第一条命中时短路，
    症状是**除了「查不到歌」，所有正常回复全部崩溃** —— 比不生效更糟。

    改为取**原始模板**（``replies.get`` 不渲染），把每处 ``{名字}`` 换成
    ``.+?``、其余字面量转义，再用 ``fullmatch`` 判定。于是「这段文本是不是
    这条失败文案的实例」成了**精确判定**，而不是猜前缀、也不是去构造一份
    假的占位符参数（那既会和真实文案漂移，也会在文案含多行时出错）。
    """
    template = replies.get(key)
    if not isinstance(template, str):
        raise replies.RepliesError(
            f"失败文案必须是字符串，{key} 实际是 {type(template).__name__}"
        )

    if template in _FAILURE_MATCHERS:
        return _FAILURE_MATCHERS[template]

    parts = _PLACEHOLDER.split(template)
    # 不含占位符 → 渲染结果就是原文，全等比较即可（不需要正则）。
    # ``re.DOTALL`` 是必须的：``judge.no_chart`` 的 {available} 跨多行。
    matcher = (
        re.compile(".+?".join(re.escape(p) for p in parts), re.DOTALL)
        if len(parts) > 1
        else None
    )
    _FAILURE_MATCHERS[template] = matcher
    return matcher


def _is_failure(text: str) -> bool:
    """这条回复是不是「参数值不对」类失败（见 :data:`_FAILURE_KEYS`）。

    用**文案比对**而不是让处理函数返回状态码：处理函数的签名是
    ``(cmd_params, miss, session_key) -> str``，改签名会波及 ``song_query`` /
    ``judge_detail`` 的公开函数（自检脚本与等价性测试都在直接调它们）。
    文案比对是这里成本最低、侵入最小的做法 —— 但**必须按模板形状比**，
    见 :func:`_failure_matcher` 里记的那次事故。
    """
    for key in _FAILURE_KEYS:
        matcher = _failure_matcher(key)
        if matcher is None:
            if text == replies.get(key):
                return True
        elif matcher.fullmatch(text):
            return True
    return False


async def handle_command(
    cmd_name, cmd_params, *, session_key: str | None = None, rich: bool = False
):
    """按指令名查表 → 校验参数个数 → 交给对应处理函数。

    **指令名大小写不敏感**（``/HELP`` == ``/help``）：查表前统一转小写。
    但「未知指令」的提示**回显用户原本的写法**，不做静默改写。

    :param cmd_name: 指令名（规范名或别名，大小写任意）
    :param cmd_params: 指令参数（``parse_command`` 切好的列表）
    :param session_key: 会话标识。**给了它才启用消歧** —— 命中多个候选
        （SD / DX 同名）时记住「等用户在选哪个」并追问；``None`` / 空串时
        退回「取首条命中」的旧行为，所以自检脚本与等价性测试不受影响。
    :param rich: 允许把结果渲染成图片（见 :func:`_render_rich`）。
    :return: 回复文本；``rich=True`` 且能出图时是 :class:`RichReply`
    """
    # cmd_name 不是字符串时（调用方误传 None）按「未知指令」处理 ——
    # 重构前的实现靠逐条 == 比较，None 自然落到兜底分支，这里保持同样结果。
    entry = _lookup(cmd_name)
    if entry is None:
        return replies.text("router.unknown_command", cmd_name=cmd_name)

    # 返回值里的「是否成功」只有补参 / 消歧路径用得上，这里丢掉即可 ——
    # 对外契约仍是「返回一段回复内容」，自检脚本与等价性测试不受影响。
    return _execute(entry, cmd_params, session_key, rich)[0]


def _execute(
    entry: Command, cmd_params: list, session_key: str | None, rich: bool = False
) -> tuple["str | RichReply", bool]:
    """执行一条已解析的指令，返回 ``(回复内容, 是否成功)``。

    「失败」的定义很窄 —— 只看**参数值不对**（见 :data:`_FAILURE_KEYS`）：
    查不到、难度写错、参数给多了。补参路径靠这个信号决定
    「要不要留住状态让用户重发一次」。

    参数个数不够**不在这里报** —— 那是 :func:`_ask_for_params` /
    :func:`_resume_pending` 的职责（它们要写状态、要换文案），
    只有**绕过补参直接调用**（``handle_command``）时才会落到这里的 ``bad_params``。

    :param rich: 是否允许把结果渲染成图片（见 :func:`_render_rich`）。
        ``False``（默认）时返回值一定是 ``str`` —— 自检脚本与等价性测试因此
        完全不受影响。
    """
    # ---- 参数个数校验 ----
    # 文案里带上正确用法，比只说「参数不对」有用得多。
    usage = replies.text(f"commands.{entry.key}")
    count = len(cmd_params)
    if count < entry.min_params:
        return replies.text("router.bad_params", usage=usage), False
    if entry.max_params is not None and count > entry.max_params:
        return replies.text("router.too_many_params", usage=usage), False

    # ``miss`` 取一次：它既作「未命中」返回值，又作 _reply_variants 的哨兵 ——
    # 取一次可保证「比较用的值」与「返回的值」是同一个字符串
    # （否则热更新恰好在两次取值之间生效时会误判）。
    miss = replies.text("song.not_found")
    text = _HANDLERS[entry.key](cmd_params, miss, session_key)
    failed = _is_failure(text)

    if rich and not failed:
        # **文本版成功了才考虑出图**：失败时（查不到 / 难度写错）根本没有图可画，
        # 而且那些提示都是一两行，本来就不存在排版问题。
        rich_reply = _render_rich(entry, cmd_params, text)
        if rich_reply is not None:
            return rich_reply, True

    return text, not failed


def _render_rich(entry: Command, cmd_params: list, fallback: str) -> "RichReply | None":
    """能出图的指令在这里出图；其余（以及渲染失败）返回 ``None``，照旧发文本。

    ==================  ==================================================
    指令 key            图片内容
    ==================  ==================================================
    ``songdata``        谱面判定细节（5 列 × 4 行的扣分表）
    ``estimate``        估分结果（判定分布 + 可上传字段）
    ==================  ==================================================

    两条都是**天然表格**、靠空格对齐在 QQ 的比例字体下必然错位的，
    也是最需要图片的。

    **不抛异常**：两个 ``render_png`` 在 PIL 缺失 / 字体缺失 / 曲库读不动 /
    取不到解 / 编码失败时一律返回 ``None``，这里就顺势降级。图片是锦上添花，
    不该让指令本身跟着挂。
    """
    if entry.key == "songdata":
        png = judge_image.render_png(cmd_params[0], cmd_params[1])
        # 文件名保持 ``judge_`` 前缀不变 —— 它只影响服务端侧的记录，
        # 但改它没有任何收益，反而会让既有的排查习惯失效。
        prefix = "judge"
    elif entry.key == "estimate":
        # 参数整理与 _h_estimate 共用 _estimate_args，
        # 保证图里和文字里是同一份记录
        song_id, difficulty, percent, stars, combo, break_p = _estimate_args(cmd_params)
        png = estimate_image.render_png(
            song_id, difficulty, percent, stars, combo, break_p)
        prefix = "estimate"
    else:
        return None

    if png is None:
        return None

    # 文件名带上曲目 id 与难度，方便服务端侧排查（不含用户输入之外的信息）
    return RichReply(
        png=png,
        filename=f"{prefix}_{cmd_params[0]}_{cmd_params[1]}.png",
        fallback=fallback,
    )


async def handle_maimai_command(cmd_name, cmd_params) -> str:
    """舞萌指令的统一入口 —— **当前一律回复「未解析的指令」**。

    :param cmd_name: :func:`parse_maimai_command` 解出的指令名。可能是 ``None``
        （``#`` 后面不是合法指令格式）。**现在用不到**，保留参数是为了将来
        接入实现时函数签名不用变。
    :param cmd_params: 同上。
    :return: ``replies.json`` 的 ``maimai.unparsed`` 文案（固定一句）

    接入真实实现时改这里：按 ``cmd_name`` 查一张 ``MAIMAI_COMMANDS`` 表，
    分发到本地保留的 maimai 服务端 API 工具链。届时记得沿用本模块已有的约定：

    * 参数个数校验复用 :class:`Command` 的 ``min_params`` / ``max_params``；
    * 文案一律进 ``replies.json``，别在这个函数里硬编码；
    * ``COMMANDS`` ↔ 处理函数的键要在 import 时校验一一对应（照抄上面的做法）。

    .. note::

       该工具链**不随仓库分发**（见 ``.gitignore``）—— 它是一个功能完整的
       上传工具，公开分发不合适。接入时从本地获取，别把它加回仓库。

       原先这里指向一份恢复说明 ``liz_bot/_已移除功能_舞萌发包.md``，
       该文件已随文档整理一并删除。
    """
    return replies.text("maimai.unparsed")


# ---------------------------------------------------------------------------
# 「等用户下一句话」—— 补参数 / 选候选，共用一套会话状态
# ---------------------------------------------------------------------------
#
# 两类等待（语义相同：这条指令还没完成，等用户补一句话）：
#   * **补参数** —— 参数不够时把下一条消息当参数（见 _resume_pending）
#   * **选候选** —— 命中多个候选（SD / DX 同名）时把下一条消息当选择
#     （见 _resume_choice）
# 状态都存在 :data:`_PENDING` 里，所以 TTL、容量淘汰、会话键、
# 「指令优先」「超时作废」两条安全阀对两者都自动适用。

#: 全局等待状态存储。会话键由调用方给（见 :func:`reply_text` 的 ``session_key``）。
_PENDING = pending.PendingStore()


def pending_store() -> pending.PendingStore:
    """取全局补参会话存储（调试 / 测试用；测试可 ``.clear()`` 复位）。"""
    return _PENDING


#: help 文案里的参数占位符：``<歌曲id>``（必填）/ ``[最小]``（可选）。
_PARAM_TOKEN = re.compile(r"[<\[]([^<>\[\]]+)[>\]]")


def _param_names(entry: Command) -> list[str]:
    """从该指令的 help 文案里抽出参数名 —— ``/添加别名 <歌名> <别名>`` → ``['歌名', '别名']``。

    参数名**只在 ``commands.<key>`` 里写一遍**，这里是**派生**而不是另立一份，
    所以不会出现「help 说两个参数、追问说三个」这种漂移。

    抽不到时返回空列表（调用方退回到不带括号的文案）。
    """
    return _PARAM_TOKEN.findall(replies.text(f"commands.{entry.key}"))


def _ask_text(entry: Command, collected: list[str]) -> str:
    """「还差几个参数」的追问文案。

    ⚠️ **不带指令头** —— 不显示 ``/添加别名 <歌名> <别名> — 添加别名`` 这样的用法行。
    用户已经在补参上下文里了，再报一遍指令名与说明是噪音；他只想知道还差什么。

    参数名按**已收到的个数跳过**，所以分次补参时每次提示的都是「接下来要给的」::

        /添加别名        → 还差 2 个参数（<歌名> <别名>）～ 直接发给我就好
        8               → 还差 1 个参数（<别名>）～ 直接发给我就好
        测试别名         → <结果>
    """
    missing = entry.min_params - len(collected)
    names = _param_names(entry)[len(collected):][:missing]
    if names:
        return replies.text(
            "router.ask_params", missing=missing, params=" ".join(names)
        )
    # 文案里没写 <...>（新增指令时忘了）—— 退回不带括号的版本，
    # 不会出现「还差 1 个参数（）～」这种半截括号。test_command_table 里有守卫。
    return replies.text("router.ask_params_noparam", missing=missing)


def _ask_for_params(
    session_key: str | None, entry: Command, collected: list[str]
) -> str:
    """参数不够 —— 记下补参状态并追问。

    ``session_key`` 为空（无状态调用）时退回原来的「参数不够」提示，
    这样 :func:`handle_command` 的直接调用方（自检脚本、等价性测试）行为不变。
    """
    if not session_key:
        # 无状态调用：没有「下一条消息」可接，只能报用法。
        # 这里的 {usage} 是**刻意保留**的 —— 用户看不到指令头就无从下手。
        return replies.text(
            "router.bad_params", usage=replies.text(f"commands.{entry.key}")
        )

    _PENDING.put(session_key, entry.key, collected)
    return _ask_text(entry, collected)


def _retry_text(entry: Command, error: str) -> str:
    """执行**失败**后的提示 —— 原始错误 + 「重发一次就好」。

    报的是**全部**参数名，而不是「还差几个」：失败时不知道是哪个参数错了
    （``/songdata 99999 紫`` 可能是 id 错，也可能是难度错），所以状态里的
    参数会被**清空**（见 :func:`_resume_pending`），用户重发一轮完整的即可。

    :param error: 原始的失败文案，可能多行（``judge.no_chart`` 会列出可用难度）
    """
    names = _param_names(entry)
    if names:
        return replies.text(
            "router.retry_params", error=error, params=" ".join(names)
        )
    # help 文案里没写 <...> —— 退回不带参数名的版本（同 _ask_text 的退化处理）
    return replies.text("router.retry_params_noparam", error=error)


def _retry_choice_text(error: str, choices: list[int]) -> str:
    """候选选择期间搜别的没搜到 —— 原始错误 + **重新列出候选**。

    候选是按 id 存进状态的，这里按 id 取回并重渲染，所以用户不用回头看
    上一条消息也能接着选。
    """
    hits = []
    for song_id in choices:
        found = song_query.select_song(str(song_id), song_query.BY_ID)
        if found:
            hits.append(found[0])
    if not hits:
        # 候选全失效了（曲库换过？）—— 只报错误，状态由调用方决定去留
        return error
    return f"{error}\n{song_query.choose_text(hits)}"


def _match_choice(message_content: str, choices: list[int]) -> int | None:
    """把用户输入解析成候选**下标**（0 基）；不是有效选择返回 ``None``。

    接受两种写法（候选行里两种信息都印出来了）::

        1 / 2 …        序号（1 基）
        70 / 10070     候选行末尾的 ``id``

    **序号优先**：``1`` 在两种解释下都成立时按序号算。这不会误伤 ——
    候选 id 最小是 8（且 DX 是五位数），而 ``8`` 只有在「序号 8」不成立时
    才会落到 id 解释，实测单次查询最多 6 个候选。

    只接受**单个整数**（``8 9`` / ``8.0`` / ``abc`` 一律不算选择）。
    """
    text = message_content.strip()
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    if 1 <= value <= len(choices):
        return value - 1
    if value in choices:
        return choices.index(value)
    return None


async def _resume_choice(
    session_key: str | None, entry: Command, state: pending.Pending,
    message_content: str, rich: bool = False,
) -> "str | RichReply":
    """用户正在「多个候选择一」—— 把这条消息当选择。

    * 能解析成序号 / id → 选中那首并渲染；
    * 解析不了 → **取消选择**，把这条消息当**新的关键词**重跑同一条指令。

    为什么第二分支是「重跑」而不是「报错」：用户完全可能选到一半改主意
    去搜别的（``/bm ジングルベル`` → 候选 → 直接输 ``会员制餐厅``）。
    把它当新关键词重跑，用户得到的是「没找到」或新结果，而不是一句
    「未知指令」；也不会把群里的正常聊天静默吃掉。

    重跑**也没找到**时（用户 2026-09-26 反馈的「无法再回复」），把候选状态
    **恢复**并重新列出候选 —— 否则用户既没选成、又搜不到，就卡死了。
    """
    index = _match_choice(message_content, state.choices or [])
    if index is None:
        _PENDING.drop(session_key)
        text, ok = _execute(entry, message_content.split(), session_key, rich)
        if ok:
            return text
        # 新关键词也没搜到 —— 回到「选候选」状态，候选重新列一遍
        choices = list(state.choices or [])
        _PENDING.put(session_key, entry.key, [], choices=choices)
        return _retry_choice_text(text, choices)

    _PENDING.drop(session_key)
    chosen = song_query.select_song(str(state.choices[index]), song_query.BY_ID)
    if not chosen:
        # 理论上不会发生（候选与本次查询来自同一份曲库快照）—— 真发生了就当作没找到
        return replies.text("song.not_found")
    return _render_hit(entry.key, chosen[0])


async def _resume_pending(
    session_key: str | None, message_content: str, rich: bool = False,
) -> "str | RichReply | None":
    """把一条**非指令**消息当作补参 / 选择。该会话没在等待则返回 ``None``。

    一条消息可以**一次补齐多个参数**（按顺序），不必一个参数一条消息::

        /添加别名        → 还差 2 个参数（歌名 别名）～ 直接发给我就好
        8 测试别名        → <结果>          ← 一轮给两个，按顺序取

    参数切分与 :func:`parse_command` 一致（``str.split()``，含全角空格与换行），
    所以「补参」和「在指令行里一次写完」的结果完全相同 —— 换句话说，
    ``/添加别名 8 测试别名`` 与「``/添加别名`` + ``8 测试别名``」等价。

    参数**追加**在已有参数之后（``state.params + 本条切出的 token``），
    因此也支持「先补一个、再补一个」的分次叠加。

    若该会话是在**选候选**（``state.choices`` 非空），这条消息按选择处理，
    见 :func:`_resume_choice`。
    """
    if not session_key:
        return None

    state = _PENDING.get(session_key)
    if state is None:
        return None

    entry = _COMMANDS_BY_KEY.get(state.cmd_key)
    if entry is None:
        # 指令表改过（版本不一致 / 热更新），旧状态作废
        _PENDING.drop(session_key)
        return None

    if state.choices is not None:
        return await _resume_choice(session_key, entry, state, message_content, rich)

    collected = state.params + message_content.split()

    if len(collected) < entry.min_params:
        # 还不够 —— **保留**状态、参数叠加，继续追问。这就是「多次回复叠加」。
        _PENDING.put(session_key, entry.key, collected)
        return _ask_text(entry, collected)

    # 够了 —— **先清掉本轮状态再执行**。
    #
    # ⚠️ 顺序不能反：处理器内部可能**写入新的状态**（命中多个候选时
    #    `_choose_prompt` 会记下「等用户在选哪个」）。先 drop 再执行，
    #    处理器写的那份才不会被这里覆盖掉。
    #
    # ⚠️ 执行时要带 session_key：补参凑齐后若命中多个候选，消歧还得靠它
    #    记状态 —— 否则「补参补齐」这条路径下 SD/DX 又会静默取首条。
    _PENDING.drop(session_key)
    text, ok = _execute(entry, collected, session_key, rich)

    if ok:
        return text

    # ---- 失败：值不对（查不到 / 难度写错 / 参数给多了）----
    # 上一版在这里**直接退出**，用户反馈「回复错误参数就退出、无法再回复」——
    # 只能把整条指令重打一遍，正是补参想省掉的那件事。
    #
    # 现在**留住状态**让用户重发一次，但**把参数清空**：
    # 失败时不知道是哪个参数错了（`/songdata 99999 紫` 可能 id 错、也可能
    # 难度错），继续往上叠只会越补越错。清空后重发一轮完整的就干净了 ——
    # 这同时天然解决了「参数给多了」：`/id` 收到 `8 9` 报错后，
    # 下一条 `8` 是全新的一轮，不会再叠成 `8 9 8`。
    _PENDING.put(session_key, entry.key, [])
    return _retry_text(entry, text)


async def reply_text(
    message_content: str, *, session_key: str | None = None, rich: bool = False
) -> "str | RichReply":
    """把一条**非空**消息变成回复内容 —— 指令前缀的分发入口。

    ================  ======================================================
    输入              去向
    ================  ======================================================
    ``#`` 开头        舞萌命名空间 → :func:`handle_maimai_command`
    ``/`` 开头        :data:`COMMANDS` → :func:`handle_command`
    其他              若该会话**正在等待**（补参数 / 选候选），这条就是
                      参数或选择（见 :mod:`liz_bot.pending`）；否则「未知指令」
    ================  ======================================================

    ``qqgroupbot`` 只调这一个函数，于是「有什么前缀」只在本模块里定义 ——
    将来加前缀不必再改 bot 外壳。

    :param message_content: 消息原文（**不用**先 strip，各解析器内部都会处理）
    :param session_key: 会话标识（本项目是 ``群 openid:成员 openid``）。
        **给了它才启用多轮补参与消歧**；``None`` / 空串表示无状态调用
        （测试、一次性调用），此时参数不够就直接回「参数不够」提示，
        命中多个候选则取首条。
    :param rich: 允许把结果渲染成图片（见 :func:`_render_rich`）。
        ``qqgroupbot`` 传 ``True``；**自检脚本与等价性测试不传**，
        于是它们拿到的永远是 ``str``，行为与加这个参数之前完全一致。
    :return: 回复文本；``rich=True`` 且能出图时是 :class:`RichReply`
    """
    # ``#`` 命名空间当前一律回「未解析」，所以哪怕格式不合法也走这里，
    # 不要掉进 bot.not_command —— 否则「# 上传」会被说成「未知指令」，
    # 让人以为 ``#`` 不被认识。
    if is_maimai_command(message_content):
        # 用户明确表达了「我要干别的」→ 取消等待
        _PENDING.drop(session_key)
        cmd_name, cmd_params, _parsed = await parse_maimai_command(message_content)
        return await handle_maimai_command(cmd_name, cmd_params)

    cmd_name, cmd_params, is_valid = await parse_command(message_content)

    if is_valid:
        # 发了新指令 → 无论原先在等什么，都先取消
        _PENDING.drop(session_key)
        entry = _lookup(cmd_name)
        if entry is not None and len(cmd_params) < entry.min_params:
            return _ask_for_params(session_key, entry, cmd_params)
        return await handle_command(
            cmd_name, cmd_params, session_key=session_key, rich=rich
        )

    # 不是指令 —— 若这个会话正在等待，这条消息就是参数 / 选择
    resumed = await _resume_pending(session_key, message_content, rich)
    if resumed is not None:
        return resumed

    return replies.text("bot.not_command", content=message_content)
