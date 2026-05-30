"""基于 pystray 的系统托盘集成。

图标在守护线程中运行；托盘菜单回调会通过 ``app.after(0, ...)``
派发回 Tk 主循环。缺少 ``pystray`` / ``Pillow`` 时会优雅降级为空操作。
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable, Optional

from . import theme as T
from .i18n import t


WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_NOTIFY = 0x040B
TRAY_TITLE = "MemCleaner"

_pystray = None
_AVAILABLE: Optional[bool] = None


def _load_pystray():
    global _pystray, _AVAILABLE
    if _AVAILABLE is False:
        return None
    if _pystray is not None:
        return _pystray
    try:
        import pystray  # type: ignore
        _pystray = pystray
        _AVAILABLE = True
        return _pystray
    except ImportError:  # pragma: no cover
        _AVAILABLE = False
        return None


def _make_icon_image(size: int = 64) -> "Image.Image":
    """使用共享标志工具生成托盘图标。"""
    from .icon import make_pil_icon
    return make_pil_icon(size)


def _usage_tooltip(used_bytes: int, total_bytes: int, percent: float) -> str:
    if total_bytes <= 0:
        return TRAY_TITLE
    return (
        f"{TRAY_TITLE} | {float(percent):.0f}% | "
        f"{T.fmt_bytes(used_bytes)} / {T.fmt_bytes(total_bytes)}"
    )


class Tray:
    """封装 pystray.Icon 及其生命周期。

    支持语言切换时重建菜单（#13）。
    """

    def __init__(
        self,
        on_show: Callable[[], None],
        on_clean: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._on_show = on_show
        self._on_clean = on_clean
        self._on_quit = on_quit
        self._icon: Optional["pystray.Icon"] = None
        self._thread: Optional[threading.Thread] = None
        self._logger = logging.getLogger("memcleaner.tray")

    @staticmethod
    def available() -> bool:
        return _load_pystray() is not None

    def start(self) -> bool:
        pystray = _load_pystray()
        if pystray is None or self._icon is not None:
            return pystray is not None

        image = _make_icon_image()
        menu = self._build_menu()
        self._icon = pystray.Icon(
            "memcleaner", icon=image, title=TRAY_TITLE, menu=menu
        )
        self._configure_windows_double_click()
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        return True

    def _configure_windows_double_click(self) -> None:
        if self._icon is None or not sys.platform.startswith("win"):
            return
        if self._icon.__class__.__module__ != "pystray._win32":
            return

        icon = self._icon
        original_notify = icon._on_notify

        def on_notify(wparam, lparam):
            if lparam == WM_LBUTTONDBLCLK:
                self._fire_show()
            elif lparam != WM_LBUTTONUP:
                original_notify(wparam, lparam)

        icon._message_handlers[WM_NOTIFY] = on_notify

    def update_menu(self) -> None:
        """#13：使用当前语言字符串重建托盘菜单。"""
        if self._icon is not None:
            try:
                self._icon.menu = self._build_menu()
                self._icon.update_menu()
            except Exception:
                pass

    def set_usage(self, used_bytes: int, total_bytes: int, percent: float) -> None:
        if self._icon is not None:
            self._icon.title = _usage_tooltip(used_bytes, total_bytes, percent)

    def _build_menu(self):
        pystray = _load_pystray()
        if pystray is None:
            return None
        return pystray.Menu(
            pystray.MenuItem(t("tray.show"), self._fire_show),
            pystray.MenuItem(t("tray.clean"), self._fire_clean),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray.quit"), self._fire_quit),
        )

    def stop(self, timeout: float = 1.5) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                self._logger.debug("tray stop failed", exc_info=True)
            self._icon = None
        if (
            self._thread is not None
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=timeout)
        self._thread = None

    # ---- 内部回调 ------------------------------------------------------

    def _fire_show(self, *_args) -> None:
        try:
            self._on_show()
        except Exception:
            self._logger.debug("tray on_show failed", exc_info=True)

    def _fire_clean(self, *_args) -> None:
        try:
            self._on_clean()
        except Exception:
            self._logger.debug("tray on_clean failed", exc_info=True)

    def _fire_quit(self, *_args) -> None:
        try:
            self._on_quit()
        except Exception:
            self._logger.debug("tray on_quit failed", exc_info=True)
