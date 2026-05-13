"""仪表盘页：环形图、历史趋势图和清理按钮。"""

from __future__ import annotations

import math
import time
import tkinter as tk
from typing import List, Optional

import customtkinter as ctk

from .. import theme as T
from ..i18n import t


class DonutGauge(tk.Canvas):
    """带中心数字补间动画的内存百分比环形仪表。"""

    SIZE = 220
    THICKNESS = 22
    TWEEN_MS = 280
    TWEEN_STEP_MS = 16

    def __init__(self, master) -> None:
        super().__init__(
            master,
            width=self.SIZE,
            height=self.SIZE,
            bg=T.color(T.SURFACE),
            highlightthickness=0,
            bd=0,
        )
        self._displayed: float = 0.0
        self._target: float = 0.0
        self._used_text = ""
        self._tween_steps_left = 0
        self._step = 0.0
        self._tween_running: bool = False
        self._tween_after_id: Optional[str] = None
        self._draw()

    def set_percent(self, percent: float, used_text: str = "") -> None:
        self._used_text = used_text
        self._target = max(0.0, min(100.0, float(percent)))
        # 取消正在进行的补间，避免动画重叠（#11）
        if self._tween_after_id is not None:
            try:
                self.after_cancel(self._tween_after_id)
            except tk.TclError:
                pass
            self._tween_after_id = None
        self._tween_running = False
        self._tween_steps_left = 0
        if self._displayed == 0.0 and self._target > 0:
            self._displayed = self._target
            self._draw()
            return
        delta = self._target - self._displayed
        if abs(delta) < 0.05:
            self._displayed = self._target
            self._draw()
            return
        self._tween_steps_left = max(1, self.TWEEN_MS // self.TWEEN_STEP_MS)
        self._step = delta / self._tween_steps_left
        self._tween_running = True
        self._tween_tick()

    def _tween_tick(self) -> None:
        if self._tween_steps_left <= 0 or not self._tween_running:
            self._displayed = self._target
            self._tween_running = False
            self._tween_after_id = None
            self._draw()
            return
        self._displayed += self._step
        self._tween_steps_left -= 1
        self._draw()
        if self._tween_steps_left > 0:
            self._tween_after_id = self.after(self.TWEEN_STEP_MS, self._tween_tick)
        else:
            self._displayed = self._target
            self._tween_running = False
            self._tween_after_id = None
            self._draw()

    def refresh_theme(self) -> None:
        self.configure(bg=T.color(T.SURFACE))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        s = self.SIZE
        thick = self.THICKNESS
        pad = thick // 2 + 4
        x0, y0, x1, y1 = pad, pad, s - pad, s - pad

        track = T.color(T.SURFACE_2)
        fg = T.percent_color(self._displayed)

        self.create_arc(
            x0, y0, x1, y1,
            start=0, extent=359.999,
            style="arc", outline=track, width=thick,
        )

        if self._displayed > 0.0:
            extent = -self._displayed * 3.6
            extent = max(-359.99, min(-0.01, extent))
            self.create_arc(
                x0, y0, x1, y1,
                start=90, extent=extent,
                style="arc", outline=fg, width=thick,
            )

        self.create_text(
            s // 2, s // 2 - 10,
            text=f"{self._displayed:.0f}%",
            fill=T.color(T.TEXT),
            font=(T.FONT_FAMILY, 40, "bold"),
        )
        if self._used_text:
            self.create_text(
                s // 2, s // 2 + 28,
                text=self._used_text,
                fill=T.color(T.TEXT_DIM),
                font=(T.FONT_FAMILY, 11),
            )


class HistoryChart(tk.Canvas):
    """最近 N 秒内存百分比的面积折线图，带悬停提示；
    数据采集中显示轻微骨架脉冲。

    #15：历史数据与上次绘制一致时跳过重绘。
    """

    HEIGHT = 220
    SKELETON_PERIOD_MS = 50

    def __init__(self, master) -> None:
        super().__init__(
            master,
            height=self.HEIGHT,
            bg=T.color(T.SURFACE),
            highlightthickness=0,
            bd=0,
        )
        self._history: List[float] = []
        self._hover_index: Optional[int] = None
        self._skeleton_phase = 0.0
        self._skeleton_running = False

        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)

    # ---- 对外接口 ------------------------------------------------------

    def set_history(self, hist: List[float]) -> None:
        new_hist = list(hist)
        # #15：仅在数据或悬停状态变化时重绘
        if (self._history == new_hist
                and len(new_hist) >= 2
                and self._hover_index is None):
            return
        self._history = new_hist
        if len(self._history) >= 2 and self._skeleton_running:
            self._skeleton_running = False
        if len(self._history) < 2 and not self._skeleton_running:
            self._skeleton_running = True
            self._pulse_tick()
        self._draw()

    def refresh_theme(self) -> None:
        self.configure(bg=T.color(T.SURFACE))
        self._draw()

    # ---- 悬停 ----------------------------------------------------------

    def _on_motion(self, event) -> None:
        if len(self._history) < 2:
            return
        w = max(1, self.winfo_width())
        pad_l, pad_r = 32, 16
        plot_w = w - pad_l - pad_r
        if plot_w <= 0:
            return
        x = event.x - pad_l
        if x < 0 or x > plot_w:
            self._hover_index = None
        else:
            n = len(self._history)
            step = plot_w / max(1, n - 1)
            idx = round(x / step)
            self._hover_index = max(0, min(n - 1, idx))
        self._draw()

    def _on_leave(self, _evt) -> None:
        if self._hover_index is not None:
            self._hover_index = None
            self._draw()

    # ---- 骨架屏 --------------------------------------------------------

    def _pulse_tick(self) -> None:
        if not self._skeleton_running:
            return
        self._skeleton_phase = (self._skeleton_phase + 0.06) % (2 * math.pi)
        self._draw()
        self.after(self.SKELETON_PERIOD_MS, self._pulse_tick)

    def _draw_skeleton(self, w: int, h: int, pad_l: int, pad_t: int, plot_w: int, plot_h: int) -> None:
        rows = 3
        gap = 14
        bar_h = 12
        total = rows * bar_h + (rows - 1) * gap
        start_y = pad_t + (plot_h - total) // 2
        base = T.color(T.SURFACE_2)
        bright = T.color(T.SURFACE_3)
        for i in range(rows):
            local_phase = self._skeleton_phase + i * 0.6
            mix = (math.sin(local_phase) + 1) / 2
            color = bright if mix > 0.55 else base
            y = start_y + i * (bar_h + gap)
            width_factor = 0.6 + 0.35 * mix
            self.create_rectangle(
                pad_l, y, pad_l + plot_w * width_factor, y + bar_h,
                fill=color, outline="",
            )
        self.create_text(
            w // 2, h - 14,
            text=t("dashboard.collecting"),
            fill=T.color(T.TEXT_MUTED),
            font=(T.FONT_FAMILY, 10),
        )

    # ---- 绘制 ----------------------------------------------------------

    def _draw(self) -> None:
        self.delete("all")
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        pad_l, pad_r, pad_t, pad_b = 32, 16, 18, 22

        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b
        if plot_w <= 4 or plot_h <= 4:
            return

        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = pad_t + plot_h * (1 - frac)
            self.create_line(
                pad_l, y, pad_l + plot_w, y,
                fill=T.color(T.SURFACE_3), dash=(2, 4),
            )
            self.create_text(
                pad_l - 6, y,
                text=f"{int(frac * 100)}",
                fill=T.color(T.TEXT_MUTED),
                font=(T.FONT_FAMILY, 9),
                anchor="e",
            )

        if len(self._history) < 2:
            self._draw_skeleton(w, h, pad_l, pad_t, plot_w, plot_h)
            return

        n = len(self._history)
        step = plot_w / max(1, n - 1)
        points = []
        for i, p in enumerate(self._history):
            x = pad_l + i * step
            y = pad_t + plot_h * (1 - max(0.0, min(100.0, p)) / 100.0)
            points.append((x, y))

        poly = [pad_l, pad_t + plot_h]
        for x, y in points:
            poly.extend([x, y])
        poly.extend([pad_l + plot_w, pad_t + plot_h])
        self.create_polygon(*poly, fill=T.color(T.SURFACE_3), outline="")

        line_color = T.percent_color(self._history[-1])
        flat = []
        for x, y in points:
            flat.extend([x, y])
        self.create_line(*flat, fill=line_color, width=2, smooth=True)

        last_x, last_y = points[-1]
        self.create_oval(
            last_x - 4, last_y - 4, last_x + 4, last_y + 4,
            fill=line_color, outline=T.color(T.SURFACE),
        )

        if self._hover_index is not None and 0 <= self._hover_index < n:
            hx, hy = points[self._hover_index]
            seconds_ago = n - 1 - self._hover_index
            pct = self._history[self._hover_index]
            self.create_line(
                hx, pad_t, hx, pad_t + plot_h,
                fill=T.color(T.TEXT_DIM), dash=(1, 3),
            )
            self.create_oval(
                hx - 4, hy - 4, hx + 4, hy + 4,
                fill=T.color(T.SURFACE),
                outline=line_color, width=2,
            )
            label = t("dashboard.tooltip", seconds=seconds_ago, percent=f"{pct:.0f}")
            tx = hx + 10
            # 使用 Canvas 字体度量测量文本宽度
            est_w = int(self.tk.call(
                "font", "measure", (T.FONT_FAMILY, 10), label
            ))
            if tx + est_w > pad_l + plot_w:
                tx = hx - 10 - est_w
            ty = max(pad_t + 6, hy - 30)
            self.create_rectangle(
                tx - 6, ty - 4, tx + est_w + 6, ty + 16,
                fill=T.color(T.SURFACE_2),
                outline=T.color(T.SURFACE_3),
            )
            self.create_text(
                tx, ty + 6,
                text=label,
                fill=T.color(T.TEXT),
                font=(T.FONT_FAMILY, 10),
                anchor="w",
            )


# #3：带统一顶部强调条的 StatPill
class StatPill(ctk.CTkFrame):
    def __init__(
        self,
        master,
        label: str,
        value: str = "—",
        accent: Optional[T.ColorTuple] = None,
    ) -> None:
        super().__init__(master, fg_color=T.SURFACE_2, corner_radius=8)
        self._accent_color = accent

        if accent:
            stripe = ctk.CTkFrame(self, fg_color=accent, height=3, corner_radius=2)
            stripe.pack(fill="x", padx=10, pady=(8, 0))

        self._label = ctk.CTkLabel(
            self, text=label, text_color=T.TEXT_MUTED,
            font=(T.FONT_FAMILY, 10),
        )
        self._label.pack(anchor="w", padx=14, pady=(8, 0))
        self._value = ctk.CTkLabel(
            self, text=value, text_color=T.TEXT,
            font=(T.FONT_FAMILY, 14, "bold"),
        )
        self._value.pack(anchor="w", padx=14, pady=(0, 10))

    def set_label(self, text: str) -> None:
        self._label.configure(text=text)

    def set_value(self, value: str) -> None:
        self._value.configure(text=value)

    def refresh_theme(self) -> None:
        self.configure(fg_color=T.SURFACE_2)
        self._label.configure(text_color=T.TEXT_MUTED)
        self._value.configure(text_color=T.TEXT)


class DashboardPage(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color=T.BG, corner_radius=0)
        self._app = app
        self._last_clean_at: Optional[float] = None
        self._last_clean_freed: float = 0.0
        self._cleaning = False
        self._standby_busy = False
        self._light_mode_initialized = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 头部
        header = ctk.CTkFrame(self, fg_color=T.BG)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        self.h_title = ctk.CTkLabel(
            header, text=t("dashboard.title"),
            text_color=T.TEXT, font=(T.FONT_FAMILY, 20, "bold"),
        )
        self.h_title.pack(side="left")
        # #1：anchor="s" 让副标题基线与标题底部对齐
        self.h_subtitle = ctk.CTkLabel(
            header, text=t("dashboard.subtitle"),
            text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 11),
        )
        self.h_subtitle.pack(side="left", padx=(12, 0), anchor="s")

        # 顶部：仪表 + 图表
        top = ctk.CTkFrame(self, fg_color=T.BG)
        top.grid(row=1, column=0, sticky="nsew", padx=24, pady=8)
        top.grid_columnconfigure(1, weight=1)
        top.grid_rowconfigure(0, weight=1)

        self.gauge_card = ctk.CTkFrame(top, fg_color=T.SURFACE, corner_radius=12)
        self.gauge_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.gauge_card_lbl = ctk.CTkLabel(
            self.gauge_card, text=t("dashboard.usage"),
            text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 11),
        )
        self.gauge_card_lbl.pack(anchor="w", padx=20, pady=(16, 0))
        # #2：对称水平内边距，让仪表在卡片中居中
        self.gauge = DonutGauge(self.gauge_card)
        self.gauge.pack(padx=20, pady=(8, 16))

        self.chart_card = ctk.CTkFrame(top, fg_color=T.SURFACE, corner_radius=12)
        self.chart_card.grid(row=0, column=1, sticky="nsew")
        self.chart_card_lbl = ctk.CTkLabel(
            self.chart_card, text=t("dashboard.trend"),
            text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 11),
        )
        self.chart_card_lbl.pack(anchor="w", padx=20, pady=(16, 0))
        self.chart = HistoryChart(self.chart_card)
        self.chart.pack(fill="both", expand=True, padx=12, pady=(8, 12))

        # 指标块（#3：所有指标块使用顶部强调条，保持视觉一致）
        pills = ctk.CTkFrame(self, fg_color=T.BG)
        pills.grid(row=2, column=0, sticky="ew", padx=24, pady=8)
        for i in range(4):
            pills.grid_columnconfigure(i, weight=1, uniform="pill")
        self.pill_total = StatPill(pills, t("dashboard.total"), accent=T.TEXT_DIM)
        self.pill_used = StatPill(pills, t("dashboard.used"), accent=T.ACCENT)
        self.pill_avail = StatPill(pills, t("dashboard.avail"), accent=T.ACCENT_2)
        self.pill_cached = StatPill(pills, t("dashboard.cached"), accent=T.TEXT_DIM)
        for i, p in enumerate(
            (self.pill_total, self.pill_used, self.pill_avail, self.pill_cached)
        ):
            p.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))

        # 操作区（#4/#5：统一圆角 10、高度 44）
        actions = ctk.CTkFrame(self, fg_color=T.BG)
        actions.grid(row=3, column=0, sticky="ew", padx=24, pady=(12, 6))
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)

        self.btn_clean = ctk.CTkButton(
            actions,
            text=t("dashboard.btn_clean"),
            command=self._on_clean_click,
            height=44,
            corner_radius=10,
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
            text_color=T.ACCENT_FG_ON,
            font=(T.FONT_FAMILY, 13, "bold"),
        )
        self.btn_clean.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.btn_standby = ctk.CTkButton(
            actions,
            text=t("dashboard.btn_standby"),
            command=self._on_standby_click,
            height=44,
            corner_radius=10,
            fg_color=T.SURFACE_2,
            hover_color=T.SURFACE_3,
            text_color=T.TEXT,
            font=(T.FONT_FAMILY, 13, "bold"),
        )
        self.btn_standby.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        if not self._app._is_admin():
            self.btn_standby.configure(state="disabled")
            self.btn_standby.bind(
                "<Button-1>",
                lambda _e: self._app.show_toast(
                    t("toast.standby_admin_required"), "warn"
                ),
            )

        # #6/#20：last_clean 使用弱化容器，并保持底部内边距对称
        self.last_clean_label = ctk.CTkLabel(
            self, text=t("dashboard.no_clean_yet"),
            text_color=T.TEXT_MUTED, font=(T.FONT_FAMILY, 10),
        )
        self.last_clean_label.grid(row=4, column=0, sticky="w", padx=24, pady=(0, 20))

    # ---- 点击处理会包裹忙碌状态 ----------------------------------------

    def _on_clean_click(self) -> None:
        if self._cleaning:
            return
        self._cleaning = True
        self.btn_clean.configure(
            state="disabled", text=t("dashboard.btn_cleaning"),
        )
        self._app.trim_now(on_done=self._on_clean_done)

    def _on_clean_done(self) -> None:
        self._cleaning = False
        self.btn_clean.configure(
            state="normal",
            text=t("dashboard.btn_clean"),
        )

    def _on_standby_click(self) -> None:
        if self._standby_busy:
            return
        self._standby_busy = True
        self.btn_standby.configure(
            state="disabled", text=t("dashboard.btn_standby_busy"),
        )
        self._app.clear_standby_now(on_done=self._on_standby_done)

    def _on_standby_done(self) -> None:
        self._standby_busy = False
        self.btn_standby.configure(
            state="normal" if self._app._is_admin() else "disabled",
            text=t("dashboard.btn_standby"),
        )

    # ---- 对外接口 ------------------------------------------------------

    def update_stats(self, stats: dict, history: list) -> None:
        total = stats["total"]
        used = stats["used"]
        avail = stats["avail"]
        cached = stats.get("cached", 0)
        percent = stats["percent"]

        self.gauge.set_percent(
            percent, used_text=f"{T.fmt_bytes(used)} / {T.fmt_bytes(total)}"
        )
        self.chart.set_history(history)

        self.pill_total.set_value(T.fmt_bytes(total))
        self.pill_used.set_value(T.fmt_bytes(used))
        self.pill_avail.set_value(T.fmt_bytes(avail))
        self.pill_cached.set_value(T.fmt_bytes(cached))

        self._refresh_last_clean()

    def set_last_clean(self, result: dict) -> None:
        self._last_clean_at = time.time()
        self._last_clean_freed = float(result.get("freed_mb", 0.0))
        self._refresh_last_clean()

    def _refresh_last_clean(self) -> None:
        if self._last_clean_at is None:
            self.last_clean_label.configure(text=t("dashboard.no_clean_yet"))
            return
        elapsed = int(time.time() - self._last_clean_at)
        ago = T.fmt_ago(elapsed)
        self.last_clean_label.configure(
            text=t(
                "dashboard.last_clean",
                freed=f"{self._last_clean_freed:.0f}",
                ago=ago,
            )
        )

    def refresh_theme(self) -> None:
        """外观模式变化后重新读取主题令牌。"""
        self.configure(fg_color=T.BG)
        self.h_title.configure(text_color=T.TEXT)
        self.h_subtitle.configure(text_color=T.TEXT_DIM)
        self.gauge_card.configure(fg_color=T.SURFACE)
        self.gauge_card_lbl.configure(text_color=T.TEXT_DIM)
        self.chart_card.configure(fg_color=T.SURFACE)
        self.chart_card_lbl.configure(text_color=T.TEXT_DIM)
        for pill in (self.pill_total, self.pill_used, self.pill_avail, self.pill_cached):
            pill.refresh_theme()
        self.btn_clean.configure(
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
        )
        self.btn_standby.configure(
            fg_color=T.SURFACE_2,
            hover_color=T.SURFACE_3,
            text_color=T.TEXT,
        )
        self.last_clean_label.configure(text_color=T.TEXT_MUTED)
        self.gauge.refresh_theme()
        self.chart.refresh_theme()

    def refresh_language(self) -> None:
        self.h_title.configure(text=t("dashboard.title"))
        self.h_subtitle.configure(text=t("dashboard.subtitle"))
        self.gauge_card_lbl.configure(text=t("dashboard.usage"))
        self.chart_card_lbl.configure(text=t("dashboard.trend"))
        self.pill_total.set_label(t("dashboard.total"))
        self.pill_used.set_label(t("dashboard.used"))
        self.pill_avail.set_label(t("dashboard.avail"))
        self.pill_cached.set_label(t("dashboard.cached"))
        if not self._cleaning:
            self.btn_clean.configure(text=t("dashboard.btn_clean"))
        if not self._standby_busy:
            self.btn_standby.configure(text=t("dashboard.btn_standby"))
        self._refresh_last_clean()

    def on_show(self) -> None:
        if (
            getattr(self._app.config_obj, "background_mode", "full") == "light"
            and not self._light_mode_initialized
        ):
            self._last_clean_at = None
            self._last_clean_freed = 0.0
            self._light_mode_initialized = True
        self._refresh_last_clean()
