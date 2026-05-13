"""UI 共享的颜色令牌、字体与辅助函数。

每个颜色都是 ``(light, dark)`` 元组；CustomTkinter 控件可直接接收这种形式，
并根据外观模式自动切换。普通 ``tk.Canvas`` 绘制需要单个字符串，
可通过 :func:`color` 解析。
"""

from __future__ import annotations

import sys
from typing import Optional, Tuple, Union

import customtkinter as ctk

ColorTuple = Tuple[str, str]
ColorLike = Union[str, ColorTuple]

# （浅色，深色）
BG: ColorTuple = ("#f3f5f8", "#1a1d24")
SURFACE: ColorTuple = ("#ffffff", "#22262f")
SURFACE_2: ColorTuple = ("#eef0f5", "#2b2f3a")
SURFACE_3: ColorTuple = ("#dde1e9", "#343a47")
SURFACE_SUBTLE: ColorTuple = ("#f8fafc", "#1f232b")
ROW_HOVER: ColorTuple = ("#f4f6fa", "#282d37")

ACCENT: ColorTuple = ("#0a936f", "#00d9a3")
ACCENT_HOVER: ColorTuple = ("#067a5b", "#00b88a")
ACCENT_SOFT: ColorTuple = ("#dff5ef", "#123a31")
# #21：拉近浅色/深色色调，让蓝色强调色更一致
ACCENT_2: ColorTuple = ("#3b6df0", "#3b82f6")

TEXT: ColorTuple = ("#1a1d24", "#e6e8ec")
TEXT_DIM: ColorTuple = ("#4a5160", "#8b91a0")
# #22：提高浅色 TEXT_MUTED 对比度，以满足白色表面的 WCAG AA
TEXT_MUTED: ColorTuple = ("#6b7180", "#6b7180")

DANGER: ColorTuple = ("#dc2626", "#ef4444")
WARN: ColorTuple = ("#d97706", "#f59e0b")
DIVIDER: ColorTuple = ("#dde1e9", "#2f3340")
GRID: ColorTuple = ("#e8ebf1", "#303542")
CHART_FILL: ColorTuple = ("#edf7f4", "#20332f")

# #18：滚动条在两种模式下都保持可见
SCROLLBAR: ColorTuple = ("#bcc2cd", "#444b5c")
SCROLLBAR_HOVER: ColorTuple = ("#9da4b3", "#5a627a")

ACCENT_FG_ON: str = "#0b1014"  # 位于 ACCENT 填充按钮上的文字颜色

_TEXT_CANDIDATES = ["Microsoft YaHei UI", "Segoe UI", "Arial"]
_ICON_CANDIDATES = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]

FONT_FAMILY = "Microsoft YaHei UI"
ICON_FONT = "Segoe Fluent Icons"


def _resolve_fonts() -> None:
    """运行时选择最佳已安装字体（Tk 根对象创建后调用一次）。"""
    import tkinter as _tk
    root = _tk._default_root
    if root is None:
        return
    available = set(root.tk.call("font", "families"))
    for f in _TEXT_CANDIDATES:
        if f in available:
            _mod = sys.modules[__name__]
            _mod.FONT_FAMILY = f
            break
    for f in _ICON_CANDIDATES:
        if f in available:
            _mod = sys.modules[__name__]
            _mod.ICON_FONT = f
            break


_captured_mode: Optional[str] = None


def refresh_mode_cache() -> None:
    """刷新缓存的外观模式；每次切换主题后调用。"""
    global _captured_mode
    _captured_mode = ctk.get_appearance_mode()


def color(c: ColorLike) -> str:
    """根据当前 CTk 模式将（浅色，深色）元组解析为单个字符串。"""
    if isinstance(c, tuple):
        global _captured_mode
        if _captured_mode is None:
            _captured_mode = ctk.get_appearance_mode()
        return c[1] if _captured_mode == "Dark" else c[0]
    return c


def percent_color(p: float) -> str:
    """根据内存占用百分比选择合适的强调色。"""
    if p >= 90:
        return color(DANGER)
    if p >= 75:
        return color(WARN)
    return color(ACCENT)


def fmt_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            val = f"{n:.1f}"
            if val.endswith(".0"):
                val = val[:-2]
            return f"{val} {unit}"
        n /= 1024.0
    val = f"{n:.1f}"
    if val.endswith(".0"):
        val = val[:-2]
    return f"{val} PB"


def fmt_ago(seconds: int) -> str:
    """将秒数格式化为紧凑、易读的时长字符串。"""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def is_dark() -> bool:
    return ctk.get_appearance_mode() == "Dark"


# 应用内使用的 Segoe Fluent Icons / MDL2 Assets 字形
ICON_DASHBOARD = ""   # 仪表盘
ICON_LIST = ""        # 项目列表
ICON_SETTINGS = ""    # 设置
ICON_CLEAR = ""       # 清除/取消
ICON_COLLAPSE = ""  # 左箭头（Segoe Fluent / MDL2）
ICON_EXPAND = ""   # 右箭头


def icon_font(size: int = 16) -> tuple:
    """返回 Fluent/MDL2 图标字形使用的字体元组。"""
    return (ICON_FONT, size)
