"""把「指令列表」渲染成 PNG —— ``/help`` 的图片版输出。

为什么要有图片版
================
``/help`` 的每一行都是「``/用法 <参数>`` — 一长句说明」。在手机 QQ 的气泡里
这些行会**折行**，而折行之后完全看不出哪句说明属于哪条指令：用法与说明之间
只有一个 ``—``，折到第二行的说明看起来像下一条指令的。文字版还有第二个问题 ——
11 条指令平铺在一屏，没有任何缩进或分组，扫读时找不到落点。

图片版把它排成**两列**（用法 / 说明），别名另起一行挂在用法下面。
折行问题不存在（宽度由我们自己定），用法与说明的归属一眼可见。

数据来源
========
**全部来自** :func:`liz_bot.command_router.help_rows` —— 一行都不另解析。
文字版（``command_router.help_reply``）与图片版共用同一份结构化数据，
不可能对不上（连别名那一行都走 ``HelpRow.alias_line``）。

⚠️ 本模块**刻意不 import** ``command_router``：那个模块要 import 本模块
（见它的 ``_render_rich``），互相 import 会形成循环导入。所以数据由调用方
传进来，本模块只负责**画**。

缓存
====
``/help`` 的内容是**静态**的（只在改指令表或 ``replies.json`` 时才变），
所以渲染结果按**内容指纹**缓存在进程内（见 :data:`_CACHE`）：第一次 ``/help``
渲染，之后直接复用。内容一变指纹就变，自动重渲染 —— 不会出现「改了文案
但图还是旧的」。这正是用户 2026-09-27 的口径「不用即时生成」。

⚠️ 缓存键必须是**内容**而不是「跑过一次没有」：``replies.json`` 支持热更新
（见 ``runtime_paths`` 的说明），用布尔标记会让热更新后的 ``/help`` 一直发旧图。

降级
====
PIL 未安装、字体找不到、编码失败 —— 任何一种都**返回 ``None``**，由调用方退回
文字版。**刻意不抛异常**：图片是锦上添花，不该让 ``/help`` 跟着挂。

用法::

    from liz_bot.command_router import help_rows, help_title, help_note
    png = help_image.render_png(help_rows(), help_title(), help_note())   # bytes | None
"""

from __future__ import annotations

import hashlib
import io
import logging

from liz_bot import judge_image as ji
from liz_bot import replies

# 与 judge_image / estimate_image 同一套惰性容错导入：没装 Pillow 时本模块
# 仍能 import（render_png 返回 None），verify_imports / 容器构建不会因为缺
# 可选依赖而失败。
try:  # pragma: no cover - 取决于环境
    from PIL import Image, ImageDraw, ImageFont
except Exception as exc:  # noqa: BLE001 - 任何导入失败都降级
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]
    _PIL_ERROR: str | None = f"{type(exc).__name__}: {exc}"
else:
    _PIL_ERROR = None

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- 字体

#: 四个字号：``(标题, 用法, 说明, 别名)``。
#:
#: 与 judge_image 的 ``_FONT_SIZES`` **不同**（那边是给数字表用的，正文比表头大）。
#: 这里的主次关系是「**用法**最要紧」—— 用户来 ``/help`` 就是为了抄那一行，
#: 所以用法用**粗体**且最大；说明是辅助，小一号；别名再小一号。
_HELP_FONT_SIZES = (34, 24, 21, 18)


def _load_fonts():
    """加载四个字号；失败返回 ``None``。

    字体路径复用 :func:`liz_bot.judge_image._font_paths`（同一套候选与
    ``LIZ_JUDGE_FONT`` 覆盖），免得两个模块各维护一份、换字体时漏改一处。
    """
    if ImageFont is None:
        return None
    regular, bold = ji._font_paths()
    if not regular:
        return None
    try:
        return tuple(
            ImageFont.truetype(path, size)
            for path, size in zip(
                (bold, bold, regular, regular), _HELP_FONT_SIZES, strict=True
            )
        )
    except OSError:
        logger.warning("help 字体加载失败：%s", regular, exc_info=True)
        return None


def available() -> bool:
    """当前环境能否渲染（PIL + 字体都在）。测试与调用方用它决定走哪条路。"""
    return _load_fonts() is not None


# --------------------------------------------------------------------------- 排版参数

_PAD = 36          # 画布内边距
_GAP = 30          # 区块之间的间距
_ROW_H = 40        # 一行指令的高度（不含别名行）
_ALIAS_H = 30      # 别名行的高度
_COL_GAP = 44      # 「用法」列与「说明」列之间的间隙
_HEAD_H = 30       # 表头行高度

#: 正文区（不含内边距）的目标宽度上限。超过它就把**说明**折行 ——
#: 手机 QQ 会把图缩到屏宽，太宽的图会让字变小到看不清。
_MAX_CONTENT_W = 900

#: 「说明」列折行后至少要有这么宽，否则说明会被挤成一条竖线。
#: 用法最长的那条（``/估分 <id> <难度> <百分比> [dx星级] [combo]``）约 500px，
#: 900 - 500 = 400，够用；真到不够时宁可整体加宽也不压说明。
_DESC_MIN_W = 340


# --------------------------------------------------------------------------- 缓存

#: 内容指纹 → PNG 字节。见模块文档的「缓存」一节。
_CACHE: dict[str, bytes] = {}

#: 最多缓存几份。实际只会有一两份（当前内容 + 刚改过的上一版），
#: 设上限纯粹是防内存无上限增长。
_CACHE_MAX = 8


def cache_key(rows, title: str, note: str) -> str:
    """算出这三份内容的指纹 —— 任何一处文案变了它都会变。

    用 ``\\x1e`` / ``\\x1f`` 当分隔符（正文里不会出现），免得
    「A 的说明 + B 的用法」和「A 的用法 + B 的说明」拼出同一个串。
    """
    payload = "\x1f".join(
        [title, note]
        + [f"{r.usage}\x1e{r.desc}\x1e{','.join(r.aliases)}" for r in rows]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def cached_png(rows, title: str, note: str) -> bytes | None:
    """取缓存（不渲染）。给测试与排查用。"""
    return _CACHE.get(cache_key(rows, title, note))


def clear_cache() -> None:
    """清空缓存。给测试用 —— 正常运行时不需要清。"""
    _CACHE.clear()


# --------------------------------------------------------------------------- 主入口

def render_png(rows, title: str, note: str) -> bytes | None:
    """渲染成 PNG 字节；任何原因不能渲染时返回 ``None``（调用方退回文字版）。

    :param rows: :class:`liz_bot.command_router.HelpRow` 序列（只含 ``listed`` 的指令）
    :param title: 标题，即 ``daily.help``
    :param note: 尾注，即 ``daily.help_maimai``
    """
    if Image is None or ImageDraw is None:
        return None

    key = cache_key(rows, title, note)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit

    fonts = _load_fonts()
    if fonts is None:
        return None

    # 编码也在 try 里 —— 本函数的契约是「**任何原因**不能渲染时返回 None」，
    # 而 image.save() 同样可能失败（PIL 内部错误、内存不足等）。
    try:
        image = _draw(rows, title, note, fonts)
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
    except Exception:  # noqa: BLE001 - 渲染失败一律降级
        logger.exception("渲染 /help 图失败：%d 条指令", len(rows))
        return None

    png = buf.getvalue()
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = png
    return png


# --------------------------------------------------------------------------- 折行

def _wrap(text: str, font, max_w: float, measure) -> list[str]:
    """按**实测像素宽度**贪心折行。

    分词规则是刻意的：

    * **CJK / 全角标点逐字断行** —— 中文本来就不靠空格分词；
    * **连续的非 CJK 字符整体不拆** —— 把 ``songalias`` 折成 ``songal`` /
      ``ias`` 会让用户以为那是两个词，``[dx星级]`` 被拆开更是读不懂。

    只用于**说明列**与尾注。用法列不折行（它短，且拆开就没法照抄了）。
    """
    if not text:
        return []

    units: list[str] = []
    buf = ""
    for ch in text:
        if ch.isspace() or ord(ch) > 0x2E80:
            if buf:
                units.append(buf)
                buf = ""
            units.append(ch)
        else:
            buf += ch
    if buf:
        units.append(buf)

    lines: list[str] = []
    cur = ""
    for unit in units:
        if cur and measure(cur + unit, font) > max_w:
            lines.append(cur.rstrip())
            cur = unit.lstrip()
        else:
            cur += unit
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


# --------------------------------------------------------------------------- 绘制

def _draw(rows, title: str, note: str, fonts) -> "Image.Image":
    """真正画图。数据全部取自入参，见 :func:`render_png` 的调用处。"""
    title_f, usage_f, desc_f, alias_f = fonts

    # 标题来自 ``daily.help``（「Liz 会这些指令：」）—— 末尾那个冒号是给**文字版**
    # 的「下面是列表」语义用的，当标题很怪，所以这里去掉。
    heading = title.rstrip("：: \t")
    head_usage = replies.text("daily.help_usage")
    head_desc = replies.text("daily.help_desc")

    # 量宽要用真字体，所以先借一个 1×1 画布当测量器
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def measure(text: str, font) -> float:
        return probe.textlength(str(text), font=font)

    # ---- 先定列宽，再折行（说明列能占多宽取决于用法列有多宽）----
    usage_w = max(
        [measure(head_usage, usage_f)] + [measure(r.usage, usage_f) for r in rows]
    ) + _COL_GAP
    desc_avail = max(_MAX_CONTENT_W - usage_w, _DESC_MIN_W)

    # 每行 → (用法, [说明行...], 别名行)
    body: list[tuple[str, list[str], str]] = [
        (r.usage, _wrap(r.desc, desc_f, desc_avail, measure), r.alias_line)
        for r in rows
    ]
    note_lines = _wrap(note, desc_f, _MAX_CONTENT_W, measure)

    desc_w = max(
        [measure(head_desc, desc_f)]
        + [measure(line, desc_f) for _, lines, _ in body for line in lines]
    )
    note_w = max((measure(line, desc_f) for line in note_lines), default=0)

    content_w = max(
        usage_w + desc_w,
        measure(heading, title_f),
        note_w,
    )
    width = int(_PAD * 2 + content_w)

    # 画在**超高**画布上、最后按内容裁剪（见 judge_image._crop）——
    # 手工推算高度太容易和绘制代码不同步。
    body_h = sum(
        _ROW_H * len(lines) + (_ALIAS_H if alias else 0)
        for _, lines, alias in body
    )
    height = int(
        _PAD * 2 + 46 + _GAP + _HEAD_H + 10
        + body_h + _GAP + _ROW_H * (len(note_lines) + 1) + 40
    )

    img = Image.new("RGB", (width, height), ji.BG)
    d = ImageDraw.Draw(img)
    x0, y = _PAD, _PAD + 10

    # ---- 标题 ----
    d.text((x0, y), heading, font=title_f, fill=ji.INK)
    y += 46 + _GAP

    # ---- 表头 + 分隔线 ----
    d.text((x0, y), head_usage, font=usage_f, fill=ji.HEAD)
    d.text((x0 + usage_w, y), head_desc, font=usage_f, fill=ji.HEAD)
    y += _HEAD_H - 8
    d.line((x0, y, x0 + content_w, y), fill=ji.GRID, width=2)
    y += 10

    # ---- 每条指令：用法（粗体）+ 说明（折行）+ 别名（更小、更浅）----
    for usage, lines, alias in body:
        d.text((x0, y + 6), usage, font=usage_f, fill=ji.INK)
        for i, line in enumerate(lines):
            d.text((x0 + usage_w, y + 6), line, font=desc_f, fill=ji.HEAD)
            if i + 1 < len(lines):
                y += _ROW_H
        y += _ROW_H
        if alias:
            # 别名挂在**用法**下面（不是说明下面）—— 它是「这一行还能怎么写」，
            # 属于用法那一列的信息。
            d.text((x0, y), alias, font=alias_f, fill=ji.MUTED)
            y += _ALIAS_H
    y += _GAP - 14

    # ---- 尾注：单独一张浅色卡片，与指令列表分开 ----
    card_h = _ROW_H * len(note_lines) + 16
    d.rounded_rectangle(
        (x0 - 14, y - 10, x0 + max(note_w, 200) + 14, y - 10 + card_h),
        radius=16, fill=ji.CARD, outline=ji.BORDER, width=2,
    )
    for line in note_lines:
        d.text((x0, y), line, font=desc_f, fill=ji.MUTED)
        y += _ROW_H

    return ji._crop(img, y)
