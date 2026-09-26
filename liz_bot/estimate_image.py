"""把「估分」结果渲染成 PNG —— ``/估分`` 的图片版输出。

为什么要有图片版
================
估分结果有两块**天然是表格**的东西：判定明细（行=音符类型、列=判定档位）和
可上传字段（字段名 + 值）。靠空格对齐在 QQ 的比例字体下必然错位 —— 与
``/songdata`` 是同一个理由，所以两者共用同一套配色、字体与裁剪逻辑
（见 :mod:`liz_bot.judge_image`）。

数值来源
========
**全部来自** :class:`liz_bot.score_estimate.Estimate`，一行都不另算 ——
图与文字版共用同一份结果，不可能对不上。文字版见
:func:`liz_bot.score_estimate.estimate_reply`。

降级
====
PIL 未安装、字体找不到、参数不认识、取不到解、编码失败 —— 任何一种都
**返回 ``None``**，由调用方退回文字版。**刻意不抛异常**：图片是锦上添花，
不该让指令本身跟着挂。

用法::

    png = render_png("143", "紫", "99.5989", "3")      # bytes | None
"""

from __future__ import annotations

import io
import logging

from liz_bot import replies, score_estimate as se
from liz_bot.judge_image import (
    BG,
    BORDER,
    CARD,
    GRID,
    HEAD,
    INK,
    MUTED,
    _crop,
    _load_fonts,
)

# 与 judge_image 同一套惰性容错导入：没装 Pillow 时本模块仍能 import
# （render_png 返回 None），verify_imports / 容器构建不会因为缺可选依赖而失败。
try:  # pragma: no cover - 取决于环境
    from PIL import Image, ImageDraw
except Exception:  # noqa: BLE001 - 任何导入失败都降级
    Image = ImageDraw = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: 判定档位 → 颜色。**与文字版的顺序无关**，纯粹是「越差越红」的视觉编码，
#: 与 :data:`liz_bot.judge_image._LOSS_STOPS` 的色系保持一致。
_RANK_COLORS: dict[str, tuple[int, int, int]] = {
    "CriticalPerfect": (22, 163, 74),     # 绿
    "Perfect": (22, 163, 74),
    "Great": (217, 119, 6),               # 琥珀
    "Good": (234, 88, 12),                # 橙
    "Miss": (220, 38, 38),                # 红
}

#: 强调色（结果行、星级徽标）。
ACCENT = (37, 99, 235)

#: 可上传字段（与 :meth:`liz_bot.score_estimate.Estimate.payload` 一一对应）。
#: ``musicId`` / ``level`` 不重复列 —— 标题行里就是它们。
_UPLOAD_FIELDS = (
    "achievement", "deluxscoreMax", "comboStatus",
    "maxCombo", "syncStatus", "isClear", "trackNo",
)


def render_png(
    song_id: str, difficulty: str, percent: str = "", stars: str = "",
    combo: str = "", break_p: str = "",
) -> bytes | None:
    """渲染成 PNG 字节；任何原因不能渲染时返回 ``None``（调用方退回文字版）。

    :param song_id: 曲目 ID（纯数字字符串）
    :param difficulty: 难度 —— ``0``-``4`` / ``basic``-``remas`` / ``绿黄红紫白``
    :param percent: 目标达成率，如 ``99.5989``（可带 ``%``）
    :param stars: 目标 DX 星级 —— ``3`` / ``3星`` / ``★★★``；空串 = 0★
    :param combo: combo 等级 —— ``FC`` / ``FC+`` / ``AP`` / ``AP+``；空串 = 不限
    :param break_p: ``x小`` / ``x小P`` 写法（AP 下 break 的小 P 数）
    """
    if Image is None or ImageDraw is None:
        return None

    fonts = _load_fonts()
    if fonts is None:
        return None

    est, _err = se.estimate(song_id, difficulty, percent, stars, combo, break_p)
    if est is None:
        return None

    # 编码也在 try 里 —— 本函数的契约是「**任何原因**不能渲染时返回 None」，
    # 而 image.save() 同样可能失败（PIL 内部错误、内存不足等）。
    try:
        image = _draw(est, fonts)
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
    except Exception:  # noqa: BLE001 - 渲染失败一律降级
        logger.exception(
            "渲染估分图失败：song_id=%r difficulty=%r percent=%r stars=%r "
            "combo=%r break_p=%r",
            song_id, difficulty, percent, stars, combo, break_p,
        )
        return None

    return buf.getvalue()


def _draw(est: se.Estimate, fonts) -> "Image.Image":
    """真正画图。数据全部取自 ``est``，见 :func:`render_png` 的调用处。"""
    title_f, sub_f, head_f, body_f = fonts
    sol = est.solution
    counts = sol.counts
    dash = replies.text("judge.placeholder")

    pad, gap, row_h, col_gap = 36, 28, 46, 34

    # ---- 文案：**与文字版共用同一批键**，所以两版不可能各说各话 ----
    title = est.title
    sub = replies.text(
        "estimate.sub",
        difficulty=est.difficulty_name,
        level=est.level if est.level is not None else dash,
        charter=est.charter if est.charter else dash,
    )
    result = replies.text(
        "estimate.result", target=se._pct(est.target), actual=se._pct(sol.achievement),
    )
    stars = replies.text(
        "estimate.stars", stars=sol.stars, want=est.want_stars,
        dx=sol.deluxscore, max_dx=est.max_deluxscore,
    )
    combo_req = replies.text(
        "estimate.combo_req",
        combo=se.COMBO_NAMES.get(est.combo, ""), label=se.combo_label(est.combo_flag),
    )
    ignored = se.ignored_note(est)
    scale = replies.text(
        "estimate.scale", normal=est.normal_theory, bonus=est.bonus_theory)
    gap_line = (
        replies.text("estimate.gap_zero") if not sol.gap
        else replies.text("estimate.gap_note", gap=se._pct(sol.gap))
    )
    upload_title = replies.text("estimate.upload_title")
    unit = replies.text("estimate.unit")
    table_zone = replies.text("estimate.table_zone")

    # ---- 判定明细表的内容 ----
    # 列头**复用 score_estimate 的常量与文案键**，免得两版各列一套列名
    heads = (replies.text("estimate.table_head"), *se._TABLE_HEADS)
    header = [table_zone, *heads]
    rows: list[list[str]] = []
    for zone in se.ZONES:
        if not est.zone_counts[zone]:
            rows.append([zone, *([dash] * len(heads))])
            continue
        rows.append([
            zone,
            str(est.zone_counts[zone]),
            *(str(counts[f"{zone}{rank}"]) for rank in se.RANKS),
        ])

    # ---- 可上传字段的内容 ----
    # ⚠️ maxCombo 走 ``se.max_combo``（有 Miss 时取区间中点），**不能**直接用
    # ``est.note_total`` —— 有 Miss 却满连是一眼假的记录。
    flag = se.combo_status(counts)
    combo_text = (
        replies.text("estimate.combo", flag=flag, label=se.combo_label(flag))
        if flag else replies.text("estimate.combo_none")
    )
    upload = list(zip(_UPLOAD_FIELDS, (
        sol.achievement, sol.deluxscore, combo_text, se.max_combo(counts), 0, "true", 1,
    )))

    # 量宽要用真字体，所以先借一个 1×1 画布当测量器
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def measure(text: str, font) -> float:
        return probe.textlength(str(text), font=font)

    zone_w = max(measure(x, head_f) for x in (table_zone, *(r[0] for r in rows))) + 10
    col_w = [
        max(
            measure(header[i], head_f),
            *(measure(row[i], body_f) for row in rows),
        ) + col_gap
        for i in range(1, len(header))
    ]
    table_w = zone_w + sum(col_w)

    field_w = max(measure(name, head_f) for name, _ in upload) + col_gap
    value_w = max(measure(value, body_f) for _, value in upload)
    upload_w = field_w + value_w

    content_w = max(
        table_w, upload_w,
        measure(title, title_f), measure(sub, sub_f),
        measure(result, body_f) + 90,            # 给右边的星级徽标留位置
        measure(stars, sub_f), measure(combo_req, sub_f), measure(scale, sub_f),
        measure(gap_line, sub_f), measure(upload_title, head_f), measure(unit, sub_f),
        measure(ignored, sub_f) if ignored else 0,
    )
    width = int(pad * 2 + content_w)
    # 画在**超高**画布上、最后按内容裁剪（见 judge_image._crop）——
    # 手工推算高度太容易和绘制代码不同步。
    height = int(pad * 2 + 46 + 32 + gap + 40 + 32 * 3 + gap + row_h * (len(rows) + 2)
                 + gap + 40 + row_h * (len(upload) + 1) + gap + 32 * 4)

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    x0, y = pad, pad + 10

    d.text((x0, y), title, font=title_f, fill=INK)
    y += 46
    d.text((x0, y), sub, font=sub_f, fill=MUTED)
    y += 32 + gap

    # ---- 结果行：目标 / 实际 + 星级徽标 ----
    d.text((x0, y), result, font=body_f, fill=INK)
    badge = f"{sol.stars}★"
    bx = x0 + content_w - measure(badge, body_f)
    d.rounded_rectangle(
        (bx - 14, y - 6, bx + measure(badge, body_f) + 14, y + 36),
        radius=12, fill=ACCENT,
    )
    d.text((bx, y), badge, font=body_f, fill=CARD)
    y += 40
    d.text((x0, y), scale, font=sub_f, fill=MUTED)
    y += 32
    d.text((x0, y), stars, font=sub_f, fill=MUTED)
    y += 32
    d.text((x0, y), combo_req, font=sub_f, fill=MUTED)
    y += 32
    if ignored:
        d.text((x0, y), ignored, font=sub_f, fill=MUTED)
        y += 32
    y += gap

    # ---- 判定明细表 ----
    x = x0 + zone_w
    for i in range(1, len(header)):
        d.text((x + col_w[i - 1] - col_gap - measure(header[i], head_f), y),
               header[i], font=head_f, fill=HEAD)
        x += col_w[i - 1]
    d.text((x0, y), table_zone, font=head_f, fill=HEAD)
    y += row_h - 12
    d.line((x0, y, x0 + table_w, y), fill=GRID, width=2)
    y += 10

    for row in rows:
        d.text((x0, y + 9), row[0], font=head_f, fill=HEAD)
        x = x0 + zone_w
        for i in range(1, len(row)):
            cell = row[i]
            # 数量列用正文色；判定档位列按「越差越红」上色
            if i == 1 or cell == dash:
                fill = INK if cell != dash else MUTED
            else:
                fill = _RANK_COLORS[se.RANKS[i - 2]]
            d.text((x + col_w[i - 1] - col_gap - measure(cell, body_f), y + 4),
                   cell, font=body_f, fill=fill)
            x += col_w[i - 1]
        y += row_h
    y += gap - 14

    # ---- 可上传字段（一张浅色卡片，与判定表分开）----
    d.text((x0, y), upload_title, font=head_f, fill=INK)
    y += 40
    d.rounded_rectangle(
        (x0 - 14, y - 12, x0 + upload_w + 14, y + row_h * len(upload) + 4),
        radius=16, fill=CARD, outline=BORDER, width=2,
    )
    for name, value in upload:
        d.text((x0, y + 6), name, font=head_f, fill=HEAD)
        d.text((x0 + field_w, y), str(value), font=body_f, fill=INK)
        y += row_h
    y += gap - 10

    for line in (gap_line, unit):
        d.text((x0, y), line, font=sub_f, fill=MUTED)
        y += 32

    return _crop(img, y)
