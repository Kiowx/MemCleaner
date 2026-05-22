"""主应用窗口。"""

from __future__ import annotations

import logging
import os
import queue
import hashlib
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from . import _core, _core_error, __version__
from . import theme as T
from .autostart import get_autostart_command, set_autostart
from .config import CONFIG_PATH, Config
from .i18n import t, tn, set_language
from .monitor import Monitor
from .scheduler import Scheduler


# ---------- HiDPI ---------------------------------------------------------


def _enable_hidpi() -> None:
    """CustomTkinter >= 5.2 会在内部处理 DPI 感知。

    手动调用 SetProcessDpiAwareness 会与 CTk 自身缩放冲突，
    并在 HiDPI 屏幕上导致双重缩放，因此这里故意不做处理。
    """
    pass


def _is_admin_process() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _request_admin_restart() -> bool:
    """通过 UAC 重新启动应用，并返回 Windows 是否接受请求。"""
    try:
        import ctypes
        executable = sys.executable
        if getattr(sys, "frozen", False):
            params = subprocess.list2cmdline(sys.argv[1:])
        else:
            params = subprocess.list2cmdline(["-m", "memcleaner", *sys.argv[1:]])
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, params, None, 1
        )
        return int(result) > 32
    except Exception:
        return False


def _same_file_digest(left: Path, right: Path) -> bool:
    try:
        if not left.exists() or not right.exists():
            return False
        if left.stat().st_size != right.stat().st_size:
            return False
        h_left = hashlib.sha256()
        h_right = hashlib.sha256()
        with left.open("rb") as f_left, right.open("rb") as f_right:
            while True:
                chunk_l = f_left.read(1024 * 1024)
                chunk_r = f_right.read(1024 * 1024)
                if not chunk_l and not chunk_r:
                    break
                h_left.update(chunk_l)
                h_right.update(chunk_r)
        return h_left.digest() == h_right.digest()
    except OSError:
        return False


def _file_digest(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


# ---------- Toast（#1：堆叠，#12：i18n）----------------------------------


class _ToastManager:
    """跟踪当前 Toast，让新 Toast 堆叠在已有 Toast 上方。"""

    def __init__(self) -> None:
        self._active: list[int] = []  # 相对于父容器的 y 坐标

    def push(self, y: int) -> None:
        self._active.append(y)

    def pop(self, y: int) -> None:
        try:
            self._active.remove(y)
        except ValueError:
            pass

    def next_y(self, parent_h: int, toast_h: int, base_y: int = 20) -> int:
        """计算下一个 Toast 的 y 坐标，避免重叠。"""
        base = base_y
        for existing_y in self._active:
            if abs(base - existing_y) < toast_h + 8:
                base = existing_y + toast_h + 8
        return max(12, min(base, max(12, parent_h - toast_h - 20)))


_toast_mgr = _ToastManager()


class Toast(ctk.CTkFrame):
    """会自动消失的应用内通知，并在同级通知上方堆叠。"""

    _DURATIONS = {"info": 2400, "warn": 3500, "error": 4500}

    def __init__(self, parent: ctk.CTkFrame, text_: str, kind: str = "info") -> None:
        super().__init__(parent)
        self._destroyed = False
        parent.update_idletasks()
        self.configure(
            fg_color=T.SURFACE_2,
            corner_radius=10,
            border_width=1,
            border_color=T.DIVIDER,
        )

        if kind == "warn":
            dot_color = T.WARN
        elif kind == "error":
            dot_color = T.DANGER
        else:
            dot_color = T.ACCENT

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=14, pady=10)

        # 使用彩色圆点指示状态，而不是左侧竖条
        dot = ctk.CTkFrame(row, fg_color=dot_color, width=8, height=8, corner_radius=4)
        dot.pack(side="left", padx=(0, 10), pady=2)
        ctk.CTkLabel(
            row, text=text_,
            text_color=T.TEXT, font=(T.FONT_FAMILY, 12),
            wraplength=max(220, min(520, parent.winfo_width() - 96)),
            justify="left",
        ).pack(side="left")

        # 所有子控件都支持点击关闭
        for w in (self, row, dot):
            w.bind("<Button-1>", lambda _e: self._destroy())
        for child in row.winfo_children():
            child.bind("<Button-1>", lambda _e: self._destroy())

        self.update_idletasks()
        h = self.winfo_height()
        ph = parent.winfo_height()
        y_rel = _toast_mgr.next_y(ph, h, base_y=max(48, int(ph * 0.05)))
        _toast_mgr.push(y_rel)
        self._toast_y = y_rel
        self.place(relx=0.5, y=y_rel, anchor="n")
        self.lift()
        self._schedule_destroy(kind)

    def _schedule_destroy(self, kind: str) -> None:
        ms = self._DURATIONS.get(kind, 2400)
        self.after(ms, self._destroy)

    def _destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        _toast_mgr.pop(self._toast_y)
        try:
            self.destroy()
        except tk.TclError:
            pass


# ---------- 侧边栏导航 ----------------------------------------------------


class IconLabelNavButton(ctk.CTkFrame):
    """带左侧强调条的导航按钮，用于标识激活状态。

    强调条使用绝对定位，避免干扰 pack 布局；
    侧边栏收缩时也能保持位置正确。
    """

    def __init__(
        self,
        master,
        text_key: str,
        icon_glyph: str,
        command: Callable[[], None],
    ) -> None:
        super().__init__(
            master, fg_color="transparent", corner_radius=8, height=42,
            cursor="hand2", border_width=0,
        )
        self.grid_propagate(False)
        self._cmd = command
        self._text_key = text_key
        self._active = False
        self._collapsed = False

        # 左侧强调指示器（默认不可见）
        self._accent = ctk.CTkFrame(
            self, fg_color="transparent", width=3, corner_radius=2,
        )
        self._accent.place(x=4, rely=0.15, relheight=0.7)
        self._accent.bind("<Button-1>", self._on_click)

        self.icon_lbl = ctk.CTkLabel(
            self, text=icon_glyph, text_color=T.TEXT_DIM,
            font=T.icon_font(16), width=24,
        )
        self.icon_lbl.pack(side="left", padx=(12, 0), pady=8)

        self.text_lbl = ctk.CTkLabel(
            self, text=t(text_key), text_color=T.TEXT_DIM,
            font=(T.FONT_FAMILY, 13), anchor="w",
        )
        self.text_lbl.pack(side="left", padx=(10, 16), pady=8, fill="x", expand=True)

        for w in (self, self.icon_lbl, self.text_lbl):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>", self._on_hover_enter)
            w.bind("<Leave>", self._on_hover_leave)

    def refresh_language(self) -> None:
        self.text_lbl.configure(text=t(self._text_key))

    def _on_click(self, _evt=None) -> None:
        if self._cmd:
            self._cmd()

    def _on_hover_enter(self, _evt) -> None:
        if not self._active:
            self.configure(fg_color=T.SURFACE_2)

    def _on_hover_leave(self, _evt) -> None:
        if not self._active:
            self.configure(fg_color="transparent")

    def set_active(self, active: bool) -> None:
        if self._active == active:
            return
        self._active = active
        if active:
            self.configure(fg_color=T.SURFACE_2)
            self._accent.configure(fg_color=T.ACCENT)
            self.icon_lbl.configure(text_color=T.ACCENT)
            self.text_lbl.configure(
                text_color=T.ACCENT, font=(T.FONT_FAMILY, 13, "bold"),
            )
        else:
            self.configure(fg_color="transparent")
            self._accent.configure(fg_color="transparent")
            self.icon_lbl.configure(text_color=T.TEXT_DIM)
            self.text_lbl.configure(
                text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 13),
            )

    def set_collapsed(self, collapsed: bool) -> None:
        """根据侧边栏收缩/展开状态调整布局。"""
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        if collapsed:
            self.text_lbl.pack_forget()
            self._accent.place_forget()
            self.icon_lbl.pack_forget()
            self.icon_lbl.pack(side="left", padx=(8, 8), pady=8)
        else:
            self.icon_lbl.pack_forget()
            self.icon_lbl.pack(side="left", padx=(12, 0), pady=8)
            if self._active:
                self._accent.place(x=4, rely=0.15, relheight=0.7)
            self.text_lbl.pack(side="left", padx=(10, 16), pady=8, fill="x", expand=True)


# ---------- 侧边栏收缩按钮（#19）------------------------------------------


class SidebarCollapseButton(ctk.CTkFrame):
    """侧边栏底部的小按钮，用于切换收缩状态。"""

    COLLAPSED_WIDTH = 56

    def __init__(self, master, on_toggle: Callable[[], None]) -> None:
        super().__init__(master, fg_color="transparent", height=30, cursor="hand2")
        self._cmd = on_toggle
        self._collapsed = False
        self.btn_label = ctk.CTkLabel(
            self, text=T.ICON_COLLAPSE,
            text_color=T.TEXT_MUTED, font=T.icon_font(12),
        )
        self.btn_label.pack(pady=4)
        for w in (self, self.btn_label):
            w.bind("<Button-1>", lambda _e: self._cmd())
            w.bind("<Enter>", lambda _e: self.btn_label.configure(text_color=T.TEXT))
            w.bind("<Leave>", lambda _e: self.btn_label.configure(text_color=T.TEXT_MUTED))

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        glyph = T.ICON_EXPAND if collapsed else T.ICON_COLLAPSE
        self.btn_label.configure(text=glyph)


# ---------- 快捷键弹窗（#6）-----------------------------------------------


class _ShortcutsPopup(ctk.CTkToplevel):
    """显示键盘快捷键的临时弹窗。"""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        wrap = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=8)
        wrap.pack(padx=2, pady=2)

        ctk.CTkLabel(
            wrap, text=t("app.shortcuts_title"),
            text_color=T.TEXT, font=(T.FONT_FAMILY, 13, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 8))

        shortcuts = [
            t("app.shortcut_clean"),
            t("app.shortcut_refresh"),
            t("app.shortcut_tabs"),
            t("app.shortcut_tray"),
            t("app.shortcut_quit"),
        ]
        for s in shortcuts:
            ctk.CTkLabel(
                wrap, text=s,
                text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 11),
            ).pack(anchor="w", padx=20, pady=2)

        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

        self.after(6000, self._safe_destroy)
        self.bind("<Escape>", lambda _e: self._safe_destroy())
        self.focus_set()
        self.grab_set()
        # 延迟绑定 FocusOut，避免 Ctrl+/ 触发弹窗后松开修饰键时
        # 立即发生闪退式关闭。
        self.after(150, lambda: self.bind("<FocusOut>", lambda _e: self._safe_destroy()))

    def _safe_destroy(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass


# ---------- 侧边栏标志 ----------------------------------------------------


class _LogoIcon(tk.Canvas):
    """与窗口/托盘图标一致的小圆形标志。"""

    def __init__(self, master, size: int = 30) -> None:
        super().__init__(
            master, width=size, height=size,
            bg=T.color(T.SURFACE), highlightthickness=0, bd=0,
        )
        self._size = size
        self._draw()

    def _draw(self) -> None:
        s = self._size
        self.delete("all")
        # 绿色圆形
        pad = 1
        self.create_oval(
            pad, pad, s - pad, s - pad,
            fill=T.color(T.ACCENT), outline="",
        )
        # 使用共享图标工具绘制白色 “M”
        from .icon import draw_m_letter
        white = T.color(("white", "white"))

        def _line(ax, ay, bx, by, sw):
            self.create_line(ax, ay, bx, by, fill=white, width=sw, capstyle="round")

        draw_m_letter(_line, s, stroke=max(2, s // 10))

    def refresh_theme(self) -> None:
        self.configure(bg=T.color(T.SURFACE))
        self._draw()


# ---------- 窗口图标（#11）------------------------------------------------


def _make_app_icon_photo(master: tk.Misc, size: int = 64) -> tk.PhotoImage:
    """通过 Pillow 生成应用图标（托盘功能已依赖 Pillow）。"""
    from PIL import ImageTk
    from .icon import make_pil_icon

    img = make_pil_icon(size)
    return ImageTk.PhotoImage(img, master=master)


def _asset_icon_path() -> Optional[Path]:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.extend([
            Path(sys.executable).with_name("memcleaner.ico"),
            Path(sys.executable).with_name("assets") / "memcleaner.ico",
            Path(getattr(sys, "_MEIPASS", "")) / "assets" / "memcleaner.ico",
        ])
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidates.append(parent / "assets" / "memcleaner.ico")
        if (parent / "Cargo.toml").exists():
            break
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _stable_icon_path() -> Optional[Path]:
    source = _asset_icon_path()
    if source is None:
        return None
    meipass = getattr(sys, "_MEIPASS", "")
    if not getattr(sys, "frozen", False) or not meipass:
        return source
    try:
        source.resolve().relative_to(Path(meipass).resolve())
    except (OSError, ValueError):
        return source
    target = CONFIG_PATH.parent / "bin" / "memcleaner.ico"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        needs_copy = not _same_file_digest(source, target)
        if needs_copy:
            temp = target.with_suffix(".ico.tmp")
            shutil.copy2(source, temp)
            os.replace(temp, target)
        return target if target.exists() else source
    except OSError:
        return target if target.exists() else source


# ---------- 应用 ----------------------------------------------------------


class App(ctk.CTk):
    _SIDEBAR_EXPANDED = 200
    _SIDEBAR_COLLAPSED = 64

    def __init__(self) -> None:
        super().__init__()

        self.config_obj = Config.load()
        self._remember_gui_exe_path()

        # 构建控件前先应用持久化语言和主题。
        set_language(self.config_obj.language)
        ctk.set_appearance_mode("light" if self.config_obj.theme == "light" else "dark")

        # #18：Tk 根对象存在后解析字体
        T._resolve_fonts()

        self.title(t("app.title"))
        self.minsize(900, 620)
        self.configure(fg_color=T.BG)

        #自适应屏幕尺寸：取屏幕 70% 宽、75% 高，但不小于 minsize
        # 优先使用持久化的窗口几何信息
        geom = self.config_obj.window_geometry
        if geom:
            try:
                self.geometry(geom)
            except Exception:
                sw = self.winfo_screenwidth()
                sh = self.winfo_screenheight()
                w = max(900, int(sw * 0.7))
                h = max(620, int(sh * 0.75))
                self.geometry(f"{w}x{h}")
        else:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            w = max(900, int(sw * 0.7))
            h = max(620, int(sh * 0.75))
            self.geometry(f"{w}x{h}")

        # #11：启动阶段不导入 Pillow，也能设置窗口图标。
        self._icon_photo: Optional[tk.PhotoImage] = None
        try:
            icon_file = _asset_icon_path()
            if icon_file is not None:
                self.iconbitmap(default=str(icon_file))
            self._icon_photo = _make_app_icon_photo(self)
            self.iconphoto(True, self._icon_photo)
        except Exception:
            self._icon_photo = None

        self._tray: Optional[object] = None
        self._rust_daemon_started = False
        self._exiting = False
        self._sidebar_collapsed = False
        self._current_page: Optional[str] = None
        self._page_show_token = 0
        self._page_switching_until = 0.0
        self._main_thread_id = threading.get_ident()
        self._trim_lock = threading.Lock()
        self._pending_callbacks: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._save_after_id: Optional[str] = None
        self._logger = logging.getLogger("memcleaner")

        self.monitor = Monitor(interval=1.0)
        self.monitor.start()
        self.scheduler = Scheduler(self.monitor, self.config_obj, trim_lock=self._trim_lock)
        self.scheduler.start()

        self._build_layout()
        self._show_page("dashboard")
        self._bind_shortcuts()
        if not self.config_obj.window_geometry:
            self.safe_after(50, self._center_on_screen)

        if self.config_obj.tray_enabled:
            self.apply_tray_setting()
        self._sync_autostart_registration()

        self.safe_after(200, self._tick)
        self.safe_after(50, self._drain_posted_callbacks)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- 布局 ----------------------------------------------------------

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 顶部状态条（#16：使用底部边框增强视觉分隔）
        self.top_bar = ctk.CTkFrame(
            self, fg_color=T.SURFACE, corner_radius=0, height=44,
            border_width=1, border_color=T.DIVIDER,
        )
        self.top_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.top_bar.grid_propagate(False)
        self.top_bar.grid_columnconfigure(1, weight=1)

        self.status_dot = ctk.CTkFrame(
            self.top_bar, fg_color=T.TEXT_MUTED, width=8, height=8, corner_radius=4,
        )
        self.status_dot.grid(row=0, column=0, sticky="w", padx=(20, 8), pady=10)

        self.status_label = ctk.CTkLabel(
            self.top_bar, text=t("app.collecting"),
            text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 12),
        )
        self.status_label.grid(row=0, column=1, sticky="w", pady=10)

        admin = self._is_admin()
        self.admin_label = ctk.CTkLabel(
            self.top_bar,
            text=t("app.admin" if admin else "app.user").replace("● ", ""),
            text_color=(T.ACCENT if admin else T.TEXT_MUTED),
            fg_color=T.ACCENT_SOFT if admin else T.SURFACE_2,
            corner_radius=8,
            padx=10,
            height=24,
            font=(T.FONT_FAMILY, 11),
        )
        self.admin_label.grid(row=0, column=2, sticky="e", padx=20)

        # 侧边栏（#19：可收缩）
        self.sidebar = ctk.CTkFrame(
            self, fg_color=T.SURFACE, corner_radius=0,
            width=self._SIDEBAR_EXPANDED,
        )
        self.sidebar.grid(row=1, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(99, weight=1)

        self.brand_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.brand_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(20, 6))
        self.brand_row.grid_columnconfigure(1, weight=1)

        # 在小画布上绘制真实应用标志（绿色圆形 + 白色 M）
        self.brand_mark = _LogoIcon(self.brand_row, size=30)
        self.brand_mark.grid(row=0, column=0, sticky="w", padx=(12, 0))

        self.brand_lbl = ctk.CTkLabel(
            self.brand_row, text=t("app.title"),
            text_color=T.TEXT, font=(T.FONT_FAMILY, 18, "bold"),
        )
        self.brand_lbl.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.version_lbl = ctk.CTkLabel(
            self.sidebar, text=f"v{__version__}",
            text_color=T.TEXT_MUTED, font=(T.FONT_FAMILY, 10),
        )
        self.version_lbl.grid(row=1, column=0, sticky="w", padx=56, pady=(0, 18))

        self.nav_buttons: dict[str, IconLabelNavButton] = {}
        for i, (key, text_key, glyph) in enumerate(
            [
                ("dashboard", "nav.dashboard", T.ICON_DASHBOARD),
                ("processes", "nav.processes", T.ICON_LIST),
                ("settings", "nav.settings", T.ICON_SETTINGS),
            ]
        ):
            btn = IconLabelNavButton(
                self.sidebar, text_key, glyph, command=lambda k=key: self._show_page(k),
            )
            btn.grid(row=2 + i, column=0, sticky="ew", padx=12, pady=3)
            self.nav_buttons[key] = btn

        # #6：快捷键提示按钮
        self.shortcuts_btn = ctk.CTkLabel(
            self.sidebar,
            text=t("app.shortcuts_title"),
            text_color=T.TEXT_MUTED,
            font=(T.FONT_FAMILY, 10),
            cursor="hand2",
        )
        self.shortcuts_btn.grid(row=98, column=0, sticky="w", padx=22, pady=(0, 6))
        self.shortcuts_btn.bind(
            "<Button-1>", lambda _e: _ShortcutsPopup(self)
        )

        # #19：侧边栏收缩按钮
        self._collapse_btn = SidebarCollapseButton(
            self.sidebar, on_toggle=self._toggle_sidebar,
        )
        self._collapse_btn.grid(row=97, column=0, sticky="ew", padx=12, pady=(0, 4))

        self.copyright_lbl = ctk.CTkLabel(
            self.sidebar, text=t("app.copyright"),
            text_color=T.TEXT_MUTED, font=(T.FONT_FAMILY, 9),
        )
        self.copyright_lbl.grid(row=100, column=0, sticky="sw", padx=22, pady=14)

        # 内容区域
        self.content = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.pages: dict[str, ctk.CTkFrame] = {}

    def _get_page(self, key: str) -> ctk.CTkFrame:
        page = self.pages.get(key)
        if page is not None:
            return page
        if key == "dashboard":
            from .ui.dashboard import DashboardPage
            page = DashboardPage(self.content, self)
        elif key == "processes":
            from .ui.processes import ProcessesPage
            page = ProcessesPage(self.content, self)
        elif key == "settings":
            from .ui.settings import SettingsPage
            page = SettingsPage(self.content, self)
        else:
            raise KeyError(key)
        page.grid(row=0, column=0, sticky="nsew")
        self.pages[key] = page
        if self.config_obj.theme:
            try:
                if hasattr(page, "refresh_theme"):
                    page.refresh_theme()
            except Exception:
                self._logger.debug("refresh_theme failed for %s", page, exc_info=True)
        return page

    # ---- 侧边栏切换（#19）---------------------------------------------

    def _toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        if self._sidebar_collapsed:
            self._apply_sidebar_state()
            self.sidebar.configure(width=self._SIDEBAR_COLLAPSED)
        else:
            self.sidebar.configure(width=self._SIDEBAR_EXPANDED)
            self._apply_sidebar_state()

    def _apply_sidebar_state(self) -> None:
        show_text = not self._sidebar_collapsed
        for btn in self.nav_buttons.values():
            btn.set_collapsed(self._sidebar_collapsed)
        if show_text:
            self.brand_lbl.configure(text=t("app.title"))
            self.brand_lbl.grid()
            self.brand_row.grid_configure(padx=12)
            self.version_lbl.grid()
        else:
            self.brand_lbl.grid_remove()
            self.brand_row.grid_configure(padx=(17, 17))
            self.version_lbl.grid_remove()
        self.copyright_lbl.configure(
            text=t("app.copyright") if show_text else ""
        )
        self.shortcuts_btn.configure(
            text=t("app.shortcuts_title") if show_text else ""
        )
        self._collapse_btn.set_collapsed(self._sidebar_collapsed)

    # ---- HiDPI / 居中 -------------------------------------------------

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        try:
            w = self.winfo_width()
            h = self.winfo_height()
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    # ---- 键盘快捷键 ----------------------------------------------------

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-l>", lambda _e: self.trim_now())
        self.bind("<Control-r>", lambda _e: self._refresh_processes_page())
        self.bind("<Control-Key-1>", lambda _e: self._show_page("dashboard"))
        self.bind("<Control-Key-2>", lambda _e: self._show_page("processes"))
        self.bind("<Control-Key-3>", lambda _e: self._show_page("settings"))
        self.bind("<Escape>", lambda _e: self._on_escape())
        self.bind("<Control-q>", lambda _e: self._quit_now())
        # #6：Ctrl+/ 显示快捷键
        self.bind("<Control-slash>", lambda _e: _ShortcutsPopup(self))

    def _on_escape(self) -> None:
        if self.config_obj.tray_enabled and self._tray is not None:
            self.withdraw()

    # ---- 辅助函数 ------------------------------------------------------

    @staticmethod
    def _is_admin() -> bool:
        return _is_admin_process()

    def _show_page(self, key: str) -> None:
        if key == self._current_page:
            return
        old_page = self.pages.get(self._current_page) if self._current_page else None
        if old_page is not None and hasattr(old_page, "on_hide"):
            old_page.on_hide()
        for k, btn in self.nav_buttons.items():
            btn.set_active(k == key)
        self._current_page = key
        self._page_switching_until = time.monotonic() + 0.2
        page = self._get_page(key)
        page.tkraise()
        if hasattr(page, "on_show"):
            self._page_show_token += 1
            token = self._page_show_token
            self.safe_after(40, lambda k=key, tk_=token: self._run_page_on_show(k, tk_))

    def _run_page_on_show(self, key: str, token: int) -> None:
        if token != self._page_show_token or key != self._current_page:
            return
        page = self.pages.get(key)
        if page is not None and hasattr(page, "on_show"):
            page.on_show()

    def _refresh_processes_page(self) -> None:
        page = self._get_page("processes")
        self._show_page("processes")
        if hasattr(page, "on_show"):
            page.on_show()

    def show_toast(self, text_: str, kind: str = "info") -> None:
        try:
            Toast(self, text_, kind=kind)
        except Exception:
            self._logger.debug("show_toast failed", exc_info=True)

    def safe_after(self, delay_ms: int, callback: Callable[[], None]) -> Optional[str]:
        """仅在根窗口仍存活时调度 Tk 回调。"""
        if self._exiting:
            return None
        if threading.get_ident() != self._main_thread_id:
            self._post(lambda: self.safe_after(delay_ms, callback))
            return "queued"
        try:
            if not self.winfo_exists():
                return None
        except tk.TclError:
            return None

        def guarded() -> None:
            if self._exiting:
                return
            try:
                if self.winfo_exists():
                    callback()
            except tk.TclError:
                pass
            except Exception:
                self._logger.debug("scheduled callback failed", exc_info=True)

        try:
            return self.after(delay_ms, guarded)
        except tk.TclError:
            return None

    def _drain_posted_callbacks(self) -> None:
        if self._exiting:
            return
        self._drain_pending_callbacks()
        self.safe_after(50, self._drain_posted_callbacks)

    def _drain_pending_callbacks(self) -> None:
        try:
            while True:
                cb = self._pending_callbacks.get_nowait()
                try:
                    cb()
                except Exception:
                    self._logger.debug("posted callback failed", exc_info=True)
        except queue.Empty:
            pass

    # ---- 清理操作 ------------------------------------------------------

    def trim_now(self, on_done: Optional[Callable[[], None]] = None) -> None:
        if not self._trim_lock.acquire(blocking=False):
            if on_done:
                on_done()
            return  # 另一个清理任务正在执行
        exclude_foreground_process = self.config_obj.exclude_foreground_process
        excluded_process_names = self.config_obj.excluded_process_names
        cleaning_mode = self.config_obj.cleaning_mode
        clear_standby_too = self.config_obj.effective_clear_standby_too()

        def work():
            try:
                result = _core.trim_all_filtered(
                    exclude_foreground_process,
                    excluded_process_names,
                    cleaning_mode,
                    clear_standby_too,
                )
                standby_ok = bool(result.get("standby_cleared", False))
            except OSError as e:
                self._post(lambda err=e: self.show_toast(
                    t("toast.error", err=err), "error"))
                if on_done:
                    self._post(on_done)
                return
            finally:
                self._trim_lock.release()

            freed = result.get("freed_mb", 0.0)
            count = result.get("trimmed", 0)
            if clear_standby_too:
                key = "toast.cleaned_with_standby_ok" if standby_ok else "toast.cleaned_with_standby_fail"
            else:
                key = "toast.cleaned"
            msg = self._format_clean_message(key, count, result)
            self._post(lambda m=msg: self.show_toast(m, "info"))
            self._post(lambda r=result: self._get_page("dashboard").set_last_clean(r))
            if on_done:
                self._post(on_done)

        threading.Thread(target=work, daemon=True).start()

    def clear_standby_now(self, on_done: Optional[Callable[[], None]] = None) -> None:
        if not self._is_admin():
            self.show_toast(t("toast.standby_admin_required"), "warn")
            if on_done:
                on_done()
            return

        def work():
            try:
                ok = bool(_core.clear_standby())
            except OSError as e:
                self._post(lambda err=e: self.show_toast(
                    t("toast.error", err=err), "error"))
                if on_done:
                    self._post(on_done)
                return
            self._post(lambda success=ok: self.show_toast(
                t("toast.standby_ok" if success else "toast.standby_fail"),
                "info" if success else "error",
            ))
            if on_done:
                self._post(on_done)

        threading.Thread(target=work, daemon=True).start()

    def _format_clean_message(self, key: str, count: int, result: dict) -> str:
        freed = float(result.get("freed_mb", 0.0))
        msg = tn(key, count, freed=f"{freed:.0f}")
        process_freed = float(result.get("process_freed_mb", 0.0))
        system_freed = float(result.get("system_freed_mb", 0.0))
        if process_freed > 0.5 or system_freed > 0.5:
            msg = (
                f"{msg}\n"
                f"{t('toast.clean_breakdown', process=f'{process_freed:.0f}', system=f'{system_freed:.0f}')}"
            )
        return msg

    def trim_pid(self, pid: int) -> None:
        def work():
            try:
                ok = bool(_core.trim_process(pid))
            except OSError:
                ok = False
            self._post(lambda success=ok: self.show_toast(
                t("toast.trimmed_pid" if success else "toast.trim_pid_failed", pid=pid),
                "info" if success else "warn",
            ))

        threading.Thread(target=work, daemon=True).start()

    def _post(self, callback: Callable[[], None]) -> None:
        """从任意线程提交回调，并在主线程执行。"""
        self._pending_callbacks.put(callback)

    def save_config(self, immediate: bool = False) -> None:
        """防抖保存配置，将快速连续变更合并为一次写入。"""
        self.scheduler.update_config(self.config_obj)
        if hasattr(self, "_save_after_id") and self._save_after_id is not None:
            try:
                self.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
            self._save_after_id = None
        if immediate:
            self.config_obj.save()
            return
        self._save_after_id = self.after(500, self._flush_config)

    def _flush_config(self) -> None:
        """实际将配置写入磁盘。"""
        self._save_after_id = None
        self.config_obj.save()

    def _flush_all_config_now(self) -> None:
        settings = getattr(self, "pages", {}).get("settings") if hasattr(self, "pages") else None
        if settings is not None and hasattr(settings, "flush_pending_config"):
            try:
                settings.flush_pending_config()
            except Exception:
                pass
        if hasattr(self, "_save_after_id") and self._save_after_id is not None:
            try:
                self.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
            self._save_after_id = None
        self.config_obj.save()

    # ---- 托盘（#13：语言更新）------------------------------------------

    def apply_tray_setting(self) -> bool:
        if self.config_obj.tray_enabled:
            if self.config_obj.background_mode == "light":
                if self._find_rust_daemon() is None:
                    self.show_toast(t("toast.background_mode_target_missing"), "error")
                    self.config_obj.tray_enabled = False
                    self.save_config()
                    return False
                if self._tray is not None:
                    self._tray.stop()
                    self._tray = None
                # GUI 可见时由 Python 调度器负责自动清理。
                # Rust 守护进程只会在关闭到轻量托盘时启动，
                # 因此两个调度器不会重复触发。
                self._stop_rust_daemon()
                return True
            if self.config_obj.background_mode == "full":
                self._stop_rust_daemon()
            if self._tray is None:
                from .tray import Tray
                self._tray = Tray(
                    on_show=lambda: self.safe_after(0, self._show_window),
                    on_clean=lambda: self.safe_after(0, self.trim_now),
                    on_quit=lambda: self.safe_after(0, self._quit_now),
                )
                started = self._tray.start()
                if not started:
                    self._tray = None
                    # #12：使用 i18n，避免硬编码中文
                    self.show_toast(t("app.tray_unavailable"), "warn")
                    self.config_obj.tray_enabled = False
                    self.save_config()
                    return False
            return True
        else:
            if self._tray is not None:
                self._tray.stop()
                self._tray = None
            self._stop_rust_daemon()
            return True

    def _find_rust_daemon(self) -> Optional[Path]:
        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            sibling = Path(sys.executable).with_name("memcleaner_daemon.exe")
            if sibling.exists():
                return sibling
            bundled = Path(getattr(sys, "_MEIPASS", "")) / "memcleaner_daemon.exe"
            if bundled.exists():
                cached = self._cache_bundled_daemon(bundled)
                if cached is not None:
                    return cached
        here = Path(__file__).resolve()
        for parent in [here.parent, *here.parents]:
            candidates.extend([
                parent / "target" / "release" / "memcleaner_daemon.exe",
                parent / "target" / "debug" / "memcleaner_daemon.exe",
            ])
            if (parent / "Cargo.toml").exists():
                break
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def build_autostart_command(self) -> Optional[str]:
        if self.config_obj.background_mode == "light":
            daemon = self._find_rust_daemon()
            if daemon is None:
                return None
            args = [str(daemon), "--ensure"]
            gui_exe = self._remember_gui_exe_path()
            if gui_exe:
                args.extend(["--gui-exe", gui_exe])
            return subprocess.list2cmdline(args)
        executable = sys.executable
        if getattr(sys, "frozen", False):
            return f'"{executable}"'
        return f'"{executable}" -m memcleaner'

    def _remember_gui_exe_path(self) -> str:
        if not getattr(sys, "frozen", False):
            return self.config_obj.gui_exe_path
        path = sys.executable
        if self.config_obj.gui_exe_path != path:
            self.config_obj.gui_exe_path = path
        return path

    def _sync_autostart_registration(self) -> None:
        command = self.build_autostart_command() if self.config_obj.autostart else None
        current = get_autostart_command()
        if not self.config_obj.autostart:
            if current is not None and not set_autostart(False):
                self.show_toast(t("toast.registry_failed"), "error")
            return
        if command is None:
            self.show_toast(t("toast.autostart_target_missing"), "error")
            return
        if current != command and not set_autostart(True, command):
            self.show_toast(t("toast.registry_failed"), "error")

    def _cache_bundled_daemon(self, bundled: Path) -> Optional[Path]:
        bin_dir = CONFIG_PATH.parent / "bin"
        target = bin_dir / "memcleaner_daemon.exe"
        try:
            bin_dir.mkdir(parents=True, exist_ok=True)
            # 快路径：大小和摘要都匹配时，避免重复计算摘要
            if target.exists() and bundled.stat().st_size == target.stat().st_size:
                if _same_file_digest(bundled, target):
                    return target
            digest = _file_digest(bundled)
            versioned = (
                bin_dir / f"memcleaner_daemon_{digest[:12]}.exe"
                if digest
                else target.with_name("memcleaner_daemon_current.exe")
            )
            if not _same_file_digest(bundled, versioned):
                temp = versioned.with_suffix(".tmp")
                shutil.copy2(bundled, temp)
                os.replace(temp, versioned)
            try:
                temp = target.with_suffix(".tmp")
                shutil.copy2(bundled, temp)
                os.replace(temp, target)
            except OSError:
                return versioned if versioned.exists() else None
            return target if target.exists() else None
        except OSError:
            return target if target.exists() else None

    def _daemon_env(self) -> dict[str, str]:
        env = os.environ.copy()
        icon_file = _stable_icon_path()
        if icon_file is not None:
            env["MEMCLEANER_ICON_ICO"] = str(icon_file)
        if getattr(sys, "frozen", False):
            env["MEMCLEANER_GUI_EXE"] = self._remember_gui_exe_path()
            meipass = str(getattr(sys, "_MEIPASS", ""))
            meipass_lower = meipass.lower()
            for key in list(env):
                value = env.get(key, "")
                if (
                    key.startswith("_PYI_")
                    or key.startswith("_MEIPASS")
                    or key.startswith("PYINSTALLER_")
                    or (meipass_lower and meipass_lower in value.lower())
                ):
                    env.pop(key, None)
        return env

    def _daemon_args(self, daemon: Path, arg: str) -> list[str]:
        args = [str(daemon), arg]
        if arg == "--ensure":
            gui_exe = self._remember_gui_exe_path()
            if gui_exe:
                args.extend(["--gui-exe", gui_exe])
        return args

    def _popen_daemon(self, daemon: Path, arg: str) -> subprocess.Popen:
        kwargs = {
            "cwd": str(daemon.parent),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
            "close_fds": True,
            "env": self._daemon_env(),
        }
        args = self._daemon_args(daemon, arg)
        if not getattr(sys, "frozen", False) or not getattr(sys, "_MEIPASS", ""):
            return subprocess.Popen(args, **kwargs)

        # PyInstaller 单文件模式会临时把 _MEIPASS 加入进程 DLL 搜索路径。
        # 仅在创建子进程时重置它，避免长期运行的 Rust 守护进程在 GUI 退出后
        # 仍占用解压目录。
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetDllDirectoryW(None)
            try:
                return subprocess.Popen(args, **kwargs)
            finally:
                kernel32.SetDllDirectoryW(str(getattr(sys, "_MEIPASS", "")))
        except Exception:
            return subprocess.Popen(args, **kwargs)

    def _ensure_rust_daemon(self) -> bool:
        daemon = self._find_rust_daemon()
        if daemon is None:
            return False
        try:
            self._flush_all_config_now()
            proc = self._popen_daemon(daemon, "--ensure")
            try:
                exit_code = proc.wait(timeout=0.35)
            except subprocess.TimeoutExpired:
                exit_code = None
            if exit_code not in (None, 0):
                return False
            self._rust_daemon_started = True
            return True
        except OSError:
            return False

    def _stop_rust_daemon(self) -> None:
        daemon = self._find_rust_daemon()
        if daemon is None:
            return
        try:
            proc = self._popen_daemon(daemon, "--quit")
            try:
                exit_code = proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                return
            if exit_code not in (0, None) and self.config_obj.auto_elevate and not self._is_admin():
                self._run_daemon_as_admin(daemon, "--quit")
        except OSError:
            pass

    def _run_daemon_as_admin(self, daemon: Path, arg: str) -> bool:
        try:
            import ctypes
            params = subprocess.list2cmdline([arg])
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(daemon), params, str(daemon.parent), 0
            )
            return int(result) > 32
        except Exception:
            return False

    def _refresh_tray_menu(self) -> None:
        """#13：语言变化时重建托盘菜单标签。"""
        if self._tray is not None:
            self._tray.update_menu()

    def _show_window(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _quit_now(self) -> None:
        self._exiting = True
        # GUI 退出前刷新所有待处理的防抖设置。
        # 这对轻量模式尤其重要，因为 Tk 销毁后 Rust 守护进程仍会继续运行。
        self._flush_all_config_now()
        # 持久化窗口几何信息
        try:
            geom = self.geometry()
            if geom:
                self.config_obj.window_geometry = geom
                self.config_obj.save()
        except Exception:
            pass
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        self.monitor.stop()
        self.scheduler.stop()
        self.destroy()

    # ---- 主题 / 语言应用 ----------------------------------------------

    def apply_theme(self, mode: str) -> None:
        ctk.set_appearance_mode("light" if mode == "light" else "dark")
        T.refresh_mode_cache()
        self._refresh_app_theme()
        for page in self.pages.values():
            if hasattr(page, "refresh_theme"):
                try:
                    page.refresh_theme()
                except Exception:
                    self._logger.debug("refresh_theme failed for %s", page, exc_info=True)

    def _refresh_app_theme(self) -> None:
        """主题变化后显式刷新应用级控件颜色。"""
        self.configure(fg_color=T.BG)
        self.top_bar.configure(fg_color=T.SURFACE, border_color=T.DIVIDER)
        self.status_dot.configure(fg_color=T.TEXT_MUTED)
        self.status_label.configure(text_color=T.TEXT_DIM)
        admin = self._is_admin()
        self.admin_label.configure(
            text_color=T.ACCENT if admin else T.TEXT_MUTED,
            fg_color=T.ACCENT_SOFT if admin else T.SURFACE_2,
        )
        self.sidebar.configure(fg_color=T.SURFACE)
        self.brand_mark.refresh_theme()
        self.brand_lbl.configure(text_color=T.TEXT)
        self.version_lbl.configure(text_color=T.TEXT_MUTED)
        self.content.configure(fg_color=T.BG)
        self.copyright_lbl.configure(text_color=T.TEXT_MUTED)
        self.shortcuts_btn.configure(text_color=T.TEXT_MUTED)

    def apply_language(self, lang: str) -> None:
        set_language(lang)
        self.title(t("app.title"))
        if not self._sidebar_collapsed:
            self.brand_lbl.configure(text=t("app.title"))
        self.copyright_lbl.configure(
            text=t("app.copyright") if not self._sidebar_collapsed else ""
        )
        admin = self._is_admin()
        self.admin_label.configure(text=t("app.admin" if admin else "app.user").replace("● ", ""))
        for btn in self.nav_buttons.values():
            btn.refresh_language()
        self.shortcuts_btn.configure(
            text=t("app.shortcuts_title") if not self._sidebar_collapsed else ""
        )
        for page in self.pages.values():
            if hasattr(page, "refresh_language"):
                try:
                    page.refresh_language()
                except Exception:
                    self._logger.debug("refresh_language failed for %s", page, exc_info=True)
        # #13：刷新托盘菜单
        self._refresh_tray_menu()

    # ---- 定时刷新 ------------------------------------------------------

    def _tick(self) -> None:
        if self._exiting:
            return

        # 消费后台线程提交的待处理回调。
        self._drain_pending_callbacks()

        latest = self.monitor.drain_latest()
        if latest is not None:
            used = latest["used"]
            total = latest["total"]
            cached = latest.get("cached", 0)
            self.status_label.configure(
                text=(
                    f"{t('dashboard.used')} {T.fmt_bytes(used)} / {T.fmt_bytes(total)}   "
                    f"({latest['percent']:.0f}%)   ·   "
                    f"{t('dashboard.cached')} {T.fmt_bytes(cached)}"
                )
            )
            self.status_dot.configure(fg_color=T.percent_color(latest["percent"]))
            if self._tray is not None:
                self._tray.set_usage(used, total, latest["percent"])
            page = self.pages.get("dashboard")
            if (
                page is not None
                and self._current_page == "dashboard"
                and time.monotonic() >= self._page_switching_until
            ):
                page.update_stats(latest, self.monitor.history())
            proc_page = self.pages.get("processes")
            if proc_page is not None:
                proc_page.set_total_memory(total)

        # 在主线程消费调度器结果（线程安全）
        for result in self.scheduler.drain_results():
            self._on_auto_clean(result)

        self.safe_after(1000, self._tick)

    def _on_auto_clean(self, result: dict) -> None:
        """处理自动清理结果，通过 _tick 从主线程调用。"""
        trigger = result.get("trigger", "auto")
        if "error" in result:
            self.show_toast(t("toast.error", err=result["error"]), "error")
            return
        freed = result.get("freed_mb", 0.0)
        count = result.get("trimmed", 0)
        key = "toast.threshold_trigger" if trigger == "threshold" else "toast.interval_trigger"
        self.show_toast(self._format_clean_message(key, count, result), "info")
        self._get_page("dashboard").set_last_clean(result)

    # ---- 关闭 ----------------------------------------------------------

    def _on_close(self) -> None:
        if self.config_obj.tray_enabled and self._tray is not None and not self._exiting:
            self.withdraw()
            return
        if (
            self.config_obj.tray_enabled
            and self.config_obj.background_mode == "light"
            and not self._exiting
        ):
            self._flush_all_config_now()
            if self._ensure_rust_daemon():
                self._quit_now()
                return
        self._quit_now()


def main() -> None:
    if sys.platform != "win32":
        print("MemCleaner is Windows only.", file=sys.stderr)
        sys.exit(1)
    if _core is None:
        import tkinter as tk
        import tkinter.messagebox as msgbox
        root = tk.Tk()
        root.withdraw()
        msgbox.showerror(
            "MemCleaner",
            f"Native module failed to load.\n\n{_core_error}\n\n"
            "Run `maturin develop --release` from the project root.",
        )
        sys.exit(1)
    cfg = Config.load()
    if cfg.auto_elevate and not _is_admin_process():
        if _request_admin_restart():
            return
        print("Failed to request administrator privileges.", file=sys.stderr)
    _enable_hidpi()
    T.refresh_mode_cache()
    app = App()
    app.mainloop()
