"""把「谱面判定细节」渲染成 PNG —— ``/songdata`` 的图片版输出。

为什么要有图片版
================
判定细节是一张 **5 列 × 4 行**的表（列=音符类型，行=判定档位）。纯文本只能靠
空格对齐，而 QQ 用**比例字体**渲染，空格宽度与字符宽度不成比例 ⇒ 表格必然错位。
图片没有这个问题，而且能**用颜色编码扣分大小**（绿 → 琥珀 → 红），这是纯文本
做不到的。

数值来源
========
**全部复用** :mod:`liz_bot.judge_detail` 的计算函数，一行都不另算 ——
否则图与文字版迟早对不上，而「两处不一致」比「两处都错」更难查。

字体
====
容器里必须装 CJK 字体，否则所有汉字都是方框（见 Dockerfile 的
``fonts-noto-cjk``）。字体路径按「Linux 容器 → Windows 本地开发」的顺序探测，
也可用环境变量 ``LIZ_JUDGE_FONT`` 覆盖（``常规路径:粗体路径``，冒号分隔；
只给一个则两者都用它）。

降级
====
PIL 未安装、字体找不到、曲库读不出来、难度不认识 —— 任何一种都**返回 ``None``**，
由调用方退回文字版。**刻意不抛异常**：图片是锦上添花，不该让查歌功能跟着挂。

用法::

    png = render_png("143", "紫")      # bytes | None
"""

from __future__ import annotations

import io
import logging
import os

from liz_bot import judge_detail as jd, replies, song_query

logger = logging.getLogger(__name__)

# PIL 惰性容错导入：没装 Pillow 时本模块仍能 import（render_png 返回 None）。
# 这样 verify_imports / 容器构建不会因为缺依赖而失败 —— 少一个可选依赖
# 不该让整个机器人起不来。
try:  # pragma: no cover - 取决于环境
    from PIL import Image, ImageDraw, ImageFont
except Exception as exc:  # noqa: BLE001 - 任何导入失败都降级
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]
    _PIL_ERROR: str | None = f"{type(exc).__name__}: {exc}"
else:
    _PIL_ERROR = None


# --------------------------------------------------------------------------- 字体

#: ``(常规, 粗体)`` 候选路径，按顺序探测。第一个能打开的就用。
#:
#: Linux 容器里靠 Dockerfile 装的 ``fonts-noto-cjk``；Windows 那两条只为本地
#: 开发方便（生产镜像里不存在）。**顺序有意义** —— Noto 的日文假名质量明显
#: 好于文泉驿，而曲名里有大量假名（``ジングルベル``、``ネコ日和。``）。
_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ),
    # 轻量备选（约 5MB vs Noto 的约 56MB）：字形差一些但够用
    (
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ),
    # 本地开发（Windows）
    ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
    ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simhei.ttf"),
)

#: 四个字号：``(标题, 副标题, 表头/行标签, 正文数字)``。
#:
#: 正文比表头大是**刻意**的：表里的数字才是主角，行标签只是索引。
_FONT_SIZES = (34, 21, 22, 27)


def _font_paths() -> tuple[str, str]:
    """返回要用的 ``(常规, 粗体)`` 路径对；找不到返回 ``("", "")``。"""
    override = os.environ.get("LIZ_JUDGE_FONT", "").strip()
    if override:
        parts = [p.strip() for p in override.split(":") if p.strip()]
        if len(parts) == 1:
            return parts[0], parts[0]
        if len(parts) >= 2:
            return parts[0], parts[1]
    for regular, bold in _FONT_CANDIDATES:
        if os.path.isfile(regular):
            return regular, bold if os.path.isfile(bold) else regular
    return "", ""


def _load_fonts():
    """加载四个字号；失败返回 ``None``。"""
    if ImageFont is None:
        return None
    regular, bold = _font_paths()
    if not regular:
        return None
    try:
        return tuple(
            ImageFont.truetype(path, size)
            for path, size in zip(
                (bold, regular, regular, bold), _FONT_SIZES, strict=True
            )
        )
    except OSError:
        logger.warning("字体加载失败：%s", regular, exc_info=True)
        return None


def available() -> bool:
    """当前环境能否渲染（PIL + 字体都在）。测试与调用方用它决定走哪条路。"""
    return _load_fonts() is not None


# --------------------------------------------------------------------------- 配色

BG = (247, 248, 250)
CARD = (255, 255, 255)
BORDER = (226, 230, 235)
INK = (31, 35, 41)
MUTED = (138, 144, 153)
HEAD = (100, 106, 115)
GRID = (238, 241, 244)

#: 扣分 → 颜色（绿 → 琥珀 → 红）。断点是**显示宽度的取舍**，不是精确刻度：
#: 只要「越大越红」这个序关系成立，读表的人就能一眼分出轻重。
_LOSS_STOPS = ((0.0, (22, 163, 74)), (0.5, (217, 119, 6)), (2.0, (220, 38, 38)))


def _loss_color(value: float) -> tuple[int, int, int]:
    """按扣分大小取颜色。"""
    for (lo, lo_rgb), (hi, hi_rgb) in zip(_LOSS_STOPS, _LOSS_STOPS[1:]):
        if value <= hi:
            span = (hi - lo) or 1.0
            t = max(0.0, min(1.0, (value - lo) / span))
            return tuple(round(a + (b - a) * t) for a, b in zip(lo_rgb, hi_rgb))
    return _LOSS_STOPS[-1][1]


# --------------------------------------------------------------------------- 渲染


def render_png(song_id: str, difficulty: str) -> bytes | None:
    """渲染成 PNG 字节；任何原因不能渲染时返回 ``None``（调用方退回文字版）。

    :param song_id: 曲目 ID（纯数字字符串）
    :param difficulty: 难度 —— ``0``-``4`` / ``basic``-``remas`` / ``绿黄红紫白``
    """
    fonts = _load_fonts()
    if fonts is None:
        return None

    index = jd.resolve_difficulty(difficulty)
    if index is None:
        return None

    try:
        hits = song_query.select_song(select_data=song_id, select_type=song_query.BY_ID)
    except (OSError, ValueError):
        logger.exception("渲染判定图：曲库载入失败 song_id=%r", song_id)
        return None
    if not hits:
        return None

    song = hits[0]["song"]
    charts = song.get("charts") or []
    if index >= len(charts):
        return None

    # 编码也在 try 里 —— 本函数的契约是「**任何原因**不能渲染时返回 None」，
    # 而 image.save() 同样可能失败（PIL 内部错误、内存不足等）。
    # 漏掉它会破坏那个契约：异常会一路冒到 qqgroupbot 的兜底分支，
    # 用户收到「出错了」而不是本来好好的文字版。
    try:
        image = _draw(song, charts[index], index, fonts)
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
    except Exception:  # noqa: BLE001 - 渲染失败一律降级，不让查歌跟着挂
        logger.exception("渲染判定图失败：song_id=%r difficulty=%r", song_id, difficulty)
        return None

    return buf.getvalue()


def _draw(song: dict, chart: dict, index: int, fonts) -> "Image.Image":
    """真正画图。数据结构见 :func:`render_png` 的调用处。"""
    title_f, sub_f, head_f, body_f = fonts
    names = jd.difficulty_names()
    counts = jd._counts(chart)
    base, bonus = jd._theory_scores(counts)
    dash = replies.text("judge.placeholder")

    pad, gap, row_h, col_gap = 36, 28, 50, 32

    # ---- 主表内容：列 = 音符类型，行 = 数量 / great / good / miss ----
    header = list(jd.COLUMN_ORDER)
    count_row = [dash if not counts[t] else str(counts[t]) for t in header]
    loss_rows = [
        [
            dash if (t == "break" or not counts[t])
            else f"{jd._normal_loss(t, rate, base):.4f}"
            for t in header
        ]
        for _key, rate in jd.NORMAL_RATE
    ]
    labels = [
        replies.text("judge.row_count"),
        *(replies.text(f"judge.row_{key}") for key, _ in jd.NORMAL_RATE),
    ]

    # 量宽要用真字体，所以先借一个 1×1 画布当测量器
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def measure(text: str, font) -> float:
        return probe.textlength(text, font=font)

    label_w = max(measure(x, head_f) for x in labels) + 10
    col_w = [
        max(
            measure(t, head_f),
            measure(count_row[i], body_f),
            *(measure(r[i], body_f) for r in loss_rows),
        ) + col_gap
        for i, t in enumerate(header)
    ]

    # ---- BREAK 明细：每行 4 档，每档竖着三行（档位 / 得分+加成 / 扣分）----
    # 「得分+加成」那一行是**必要**的，不是装饰：100p/75p/50p 的音符得分相同
    # （都 2500）只差加成，great1/2/3 反过来加成相同只差得分。不写出来，
    # 用户看到 8 个不同扣分却无从判断为什么不同。
    break_cells = []
    for tier, score_rate, bonus_rate in jd.BREAK_TIERS:
        loss = jd._break_loss(score_rate, bonus_rate, base, bonus)
        break_cells.append((
            tier,
            f"{int(jd.BASE_SCORE['break'] * score_rate)}+{int(jd.BREAK_BONUS_MAX * bonus_rate)}",
            f"{loss:.4f}",
            loss,
        ))
    bcol_w: list[float] = []
    for start in range(0, len(break_cells), jd.BREAK_PER_ROW):
        for i, cell in enumerate(break_cells[start:start + jd.BREAK_PER_ROW]):
            w = max(measure(cell[0], head_f), measure(cell[1], body_f),
                    measure(cell[2], body_f)) + col_gap
            if i < len(bcol_w):
                bcol_w[i] = max(bcol_w[i], w)
            else:
                bcol_w.append(w)

    # ---- 文案：**与文字版共用同一批键**，所以两版不可能各说各话 ----
    level = (song.get("level") or [None] * len(charts))[index]
    charter = chart.get("charter") if isinstance(chart, dict) else None
    title = replies.text(
        "judge.header",
        title=song.get("title"),
        difficulty=names[index] if index < len(names) else str(index),
        level=level if level is not None else dash,
        charter=charter if charter else dash,
    )
    theory = replies.text("judge.scale", base_score=base, bonus_score=bonus)

    main_w = label_w + sum(col_w)
    content_w = max(main_w, sum(bcol_w), measure(title, title_f), measure(theory, sub_f))
    width = int(pad * 2 + content_w)
    # 画在**超高**画布上、最后按内容裁剪（见 _crop）—— 手工推算高度太容易和
    # 绘制代码不同步，第一版就多留了 140px 空白。
    height = int(pad * 2 + 46 + 32 + gap + row_h * 5 + gap + 62 + 90 * 4 + pad)

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    x0, y = pad, pad + 10

    d.text((x0, y), title, font=title_f, fill=INK)
    y += 46
    d.text((x0, y), theory, font=sub_f, fill=MUTED)
    y += 32 + gap

    # 表头
    x = x0 + label_w
    for i, t in enumerate(header):
        d.text((x + col_w[i] - col_gap - measure(t, head_f), y), t, font=head_f, fill=HEAD)
        x += col_w[i]
    y += row_h - 12
    d.line((x0, y, x0 + main_w, y), fill=GRID, width=2)
    y += 10

    for label, cells, is_loss in (
        [(labels[0], count_row, False)]
        + [(labels[i + 1], loss_rows[i], True) for i in range(len(loss_rows))]
    ):
        d.text((x0, y + 9), label, font=head_f, fill=HEAD)
        x = x0 + label_w
        for i, cell in enumerate(cells):
            fill = INK if (not is_loss or cell == dash) else _loss_color(float(cell))
            d.text((x + col_w[i] - col_gap - measure(cell, body_f), y + 4),
                   cell, font=body_f, fill=fill)
            x += col_w[i]
        y += row_h
    y += gap - 14

    # 单位说明（数字没带 %，靠这几行解释）—— 复用文字版的键，两行都画
    for line in replies.text("judge.unit").splitlines():
        d.text((x0, y), line, font=sub_f, fill=MUTED)
        y += 32
    y += gap - 24

    if not counts["break"]:
        d.text((x0, y), replies.text("judge.no_break"), font=sub_f, fill=MUTED)
        return _crop(img, y + 34)

    d.text((x0, y), replies.text("judge.break_title", count=counts["break"]),
           font=head_f, fill=INK)
    y += 32
    d.text((x0, y), replies.text("judge.break_hint"), font=sub_f, fill=MUTED)
    y += 38

    for start in range(0, len(break_cells), jd.BREAK_PER_ROW):
        for i, (tier, score, loss_text, loss_val) in enumerate(
            break_cells[start:start + jd.BREAK_PER_ROW]
        ):
            right = x0 + sum(bcol_w[:i]) + bcol_w[i] - col_gap
            d.text((right - measure(tier, head_f), y), tier, font=head_f, fill=HEAD)
            d.text((right - measure(score, body_f), y + 28), score, font=body_f, fill=INK)
            d.text((right - measure(loss_text, body_f), y + 58), loss_text,
                   font=body_f, fill=_loss_color(loss_val))
        y += 90
    return _crop(img, y)


def _crop(img: "Image.Image", bottom: float) -> "Image.Image":
    """按实际内容裁掉多余空白，并补画一次边框（否则圆角会被裁掉）。"""
    height = int(bottom + 30)
    cropped = img.crop((0, 0, img.width, height))
    ImageDraw.Draw(cropped).rounded_rectangle(
        (8, 8, cropped.width - 8, height - 8), radius=18, fill=None,
        outline=BORDER, width=2,
    )
    return cropped
