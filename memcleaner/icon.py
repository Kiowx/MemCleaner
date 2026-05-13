"""共享图标生成逻辑，是 MemCleaner 标志的单一来源。

使用位置：
- ``tray.py``：系统托盘图标（PIL Image）
- ``app.py``：窗口图标（ICO 字节）和侧边栏标志（tk.Canvas）
"""

from __future__ import annotations

from typing import Optional, Tuple

# 品牌色：整个应用统一使用的强调绿色。
ICON_COLOR: Tuple[int, int, int, int] = (0, 217, 163, 255)
ICON_WHITE: Tuple[int, int, int, int] = (255, 255, 255, 255)


def draw_m_letter(
    draw_line,
    size: int,
    stroke: Optional[int] = None,
) -> None:
    """在任意绘图后端中绘制字母 “M”。

    对四条线段分别调用 ``draw_line(x0, y0, x1, y1, stroke_width)``。
    """
    cx, cy = size / 2, size / 2
    w = size * 0.42
    h = size * 0.42
    x0 = cx - w / 2
    x1 = cx + w / 2
    y0 = cy - h / 2
    y1 = cy + h / 2
    sw = stroke if stroke is not None else max(2, size // 16)

    draw_line(x0, y1, x0, y0, sw)
    draw_line(x0, y0, cx, y1 - h * 0.2, sw)
    draw_line(cx, y1 - h * 0.2, x1, y0, sw)
    draw_line(x1, y0, x1, y1, sw)


def make_pil_icon(size: int = 64) -> "Image.Image":
    """生成 PIL 图标图像（绿色圆形 + 白色 M）。"""
    from PIL import Image, ImageDraw  # type: ignore

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 2
    d.ellipse([pad, pad, size - pad, size - pad], fill=ICON_COLOR)

    def draw_line(ax, ay, bx, by, sw):
        d.line([(ax, ay), (bx, by)], fill=ICON_WHITE, width=sw)

    draw_m_letter(draw_line, size)
    return img
