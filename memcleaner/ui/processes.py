"""进程列表页：展示内存占用最高的进程，支持点击清理和列排序。

#3：搜索框带清除按钮。
#4：进程列表支持方向键导航和 Enter 清理。
#5：右键上下文菜单（复制 PID、复制名称、清理）。
#10：无数据时显示加载骨架和空状态标签。
#16：基于差异渲染，只更新 PID 数据变化的行。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

import customtkinter as ctk
import tkinter as tk

from .. import _core
from .. import theme as T
from ..i18n import t


class ProcessRow(ctk.CTkFrame):
    """进程列表中的单行。"""

    def __init__(self, master, app, share_ref: Callable[[], int]) -> None:
        super().__init__(master, fg_color="transparent", height=40)
        self.grid_propagate(False)
        self._app = app
        self._pid = 0
        self._name = ""
        self._ws = 0
        self._last_total = 0
        self._share_ref = share_ref
        self._selected = False
        self._excluded = False

        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=2)

        self.pid_lbl = ctk.CTkLabel(
            self, text="", text_color=T.TEXT_MUTED,
            width=80, anchor="w", font=(T.FONT_FAMILY, 11),
        )
        self.pid_lbl.grid(row=0, column=0, padx=(14, 6), pady=6, sticky="w")

        self.name_lbl = ctk.CTkLabel(
            self, text="", text_color=T.TEXT, anchor="w",
            font=(T.FONT_FAMILY, 12),
        )
        self.name_lbl.grid(row=0, column=1, padx=6, pady=6, sticky="ew")

        bar_wrap = ctk.CTkFrame(self, fg_color="transparent")
        bar_wrap.grid(row=0, column=2, padx=6, pady=6, sticky="ew")
        bar_wrap.grid_columnconfigure(0, weight=1)
        # #7：进度条从 6px 调整为 8px，增强可见性
        self.bar = ctk.CTkProgressBar(
            bar_wrap, height=8,
            fg_color=T.SURFACE_3, progress_color=T.ACCENT,
        )
        self.bar.grid(row=0, column=0, sticky="ew", pady=(8, 0))
        self.bar.set(0)

        self.ws_lbl = ctk.CTkLabel(
            self, text="", text_color=T.TEXT_DIM,
            width=110, anchor="e", font=(T.FONT_FAMILY, 11),
        )
        self.ws_lbl.grid(row=0, column=3, padx=6, pady=6, sticky="e")

        self.btn = ctk.CTkButton(
            self, text=t("processes.trim"),
            width=64, height=26, corner_radius=8,
            fg_color=T.SURFACE_2, hover_color=T.ACCENT,
            text_color=T.TEXT, font=(T.FONT_FAMILY, 11),
            command=self._on_trim,
        )
        self.btn.grid(row=0, column=4, padx=(6, 14), pady=6, sticky="e")

        # #5：右键上下文菜单
        self._menu = tk.Menu(self, tearoff=0)
        self._menu.add_command(
            label=t("processes.trim_action"), command=self._on_trim,
        )
        self._menu.add_command(
            label=t("processes.exclude_action"), command=self._toggle_excluded,
        )
        self._menu.add_separator()
        self._menu.add_command(
            label=t("processes.copy_pid"), command=self._copy_pid,
        )
        self._menu.add_command(
            label=t("processes.copy_name"), command=self._copy_name,
        )
        for w in (self, self.pid_lbl, self.name_lbl, self.ws_lbl, self.bar, self.btn):
            w.bind("<Button-3>", self._on_right_click)

    def update_proc(self, pid: int, name: str, ws: int, excluded: bool = False) -> None:
        self._pid = pid
        self._name = name
        self._ws = ws
        self.pid_lbl.configure(text=str(pid))
        self.ws_lbl.configure(text=T.fmt_bytes(ws))
        self.set_excluded(excluded)
        self._update_bar()

    def _update_bar(self) -> None:
        total = self._share_ref()
        self._last_total = total
        if total > 0:
            ratio = min(1.0, self._ws / total)
            self.bar.set(ratio)
            # #11：按占比百分比分级显示进度条颜色
            pct = ratio * 100
            self.bar.configure(progress_color=T.percent_color(pct))
        else:
            self.bar.set(0)

    def matches(self, pid: int, name: str, ws: int, total: int, excluded: bool = False) -> bool:
        """快速检查当前行数据是否与给定值一致（#16）。"""
        return (
            self._pid == pid
            and self._name == name
            and self._ws == ws
            and self._last_total == total
            and self._excluded == excluded
        )

    def set_excluded(self, excluded: bool) -> None:
        self._excluded = excluded
        if excluded:
            self.name_lbl.configure(
                text=f"{self._name} · {t('processes.excluded_badge')}",
                text_color=T.TEXT_MUTED,
            )
            self.bar.configure(progress_color=T.TEXT_DIM)
        else:
            self.name_lbl.configure(text=self._name, text_color=T.TEXT)
            self._update_bar()

    def set_selected(self, selected: bool) -> None:
        """#4：为键盘选中的行显示视觉高亮。"""
        self._selected = selected
        fg = T.SURFACE_2 if selected else "transparent"
        self.configure(fg_color=fg)

    def refresh_language(self) -> None:
        self.btn.configure(text=t("processes.trim"))
        self._menu.entryconfigure(0, label=t("processes.trim_action"))
        self._menu.entryconfigure(
            1,
            label=t("processes.unexclude_action" if self._excluded else "processes.exclude_action"),
        )
        self._menu.entryconfigure(3, label=t("processes.copy_pid"))
        self._menu.entryconfigure(4, label=t("processes.copy_name"))
        self.set_excluded(self._excluded)

    def refresh_theme(self) -> None:
        self.pid_lbl.configure(text_color=T.TEXT_MUTED)
        self.name_lbl.configure(text_color=T.TEXT_MUTED if self._excluded else T.TEXT)
        self.ws_lbl.configure(text_color=T.TEXT_DIM)
        self.bar.configure(fg_color=T.SURFACE_3)
        self.btn.configure(
            fg_color=T.SURFACE_2,
            hover_color=T.ACCENT,
            text_color=T.TEXT,
        )
        self.set_selected(self._selected)
        self._update_bar()
        self.set_excluded(self._excluded)

    def _on_trim(self) -> None:
        if self._pid:
            self._app.trim_pid(self._pid)
            self.btn.configure(fg_color=T.ACCENT, text_color=T.ACCENT_FG_ON)
            self.after(800, self._reset_trim_btn)

    def _reset_trim_btn(self) -> None:
        self.btn.configure(fg_color=T.SURFACE_2, text_color=T.TEXT)

    def _on_right_click(self, event) -> None:
        """#5：显示上下文菜单。"""
        self._menu.entryconfigure(
            1,
            label=t("processes.unexclude_action" if self._excluded else "processes.exclude_action"),
        )
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _toggle_excluded(self) -> None:
        if not self._name:
            return
        page = self.master
        while page is not None and not isinstance(page, ProcessesPage):
            page = page.master
        if page is not None:
            page.set_process_excluded(self._name, not self._excluded)

    def _copy_pid(self) -> None:
        if self._pid:
            self.clipboard_clear()
            self.clipboard_append(str(self._pid))

    def _copy_name(self) -> None:
        if self._name:
            self.clipboard_clear()
            self.clipboard_append(self._name)


SortKey = str  # 可选："ws" | "pid" | "name"


class ProcessesPage(ctk.CTkFrame):
    REFRESH_SEC = 3.0
    SORT_GLYPHS = {"asc": " ▲", "desc": " ▼", None: ""}

    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color=T.BG, corner_radius=0)
        self._app = app
        self._procs: List[dict] = []
        self._filter = ""
        self._total_mem: int = 1
        self._last_fetch = 0.0
        self._fetch_lock = threading.Lock()
        self._fetch_generation = 0
        self._sort_key: SortKey = "ws"
        self._sort_desc: bool = True
        self._header_buttons: dict[SortKey, ctk.CTkButton] = {}
        self._selected_index: int = -1  # #4：键盘选择
        self._has_data = False           # #10：跟踪数据状态
        self._active = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 头部
        header = ctk.CTkFrame(self, fg_color=T.BG)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        self.h_title = ctk.CTkLabel(
            header, text=t("processes.title"),
            text_color=T.TEXT, font=(T.FONT_FAMILY, 20, "bold"),
        )
        self.h_title.pack(side="left")
        self.h_subtitle = ctk.CTkLabel(
            header, text=t("processes.subtitle"),
            text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 11),
        )
        self.h_subtitle.pack(side="left", padx=(12, 0), pady=(6, 0))

        # 过滤器 + 数量（#3：带清除按钮）
        bar = ctk.CTkFrame(self, fg_color=T.BG)
        bar.grid(row=1, column=0, sticky="ew", padx=24, pady=(4, 8))
        bar.grid_columnconfigure(0, weight=1)

        search_wrap = ctk.CTkFrame(bar, fg_color="transparent")
        search_wrap.grid(row=0, column=0, sticky="ew")
        search_wrap.grid_columnconfigure(0, weight=1)

        self.search = ctk.CTkEntry(
            search_wrap, placeholder_text=t("processes.search"),
            fg_color=T.SURFACE, border_color=T.SURFACE_2,
            text_color=T.TEXT, height=34, corner_radius=10,
        )
        self.search.grid(row=0, column=0, sticky="ew")
        self.search.bind("<KeyRelease>", self._on_search)

        # #3：清除按钮
        self.clear_btn = ctk.CTkButton(
            search_wrap,
            text=T.ICON_CLEAR,
            width=30, height=30,
            corner_radius=8,
            fg_color="transparent",
            hover_color=T.SURFACE_2,
            text_color=T.TEXT_MUTED,
            font=T.icon_font(12),
            command=self._clear_search,
        )
        self.clear_btn.grid(row=0, column=1, padx=(4, 0))
        self.clear_btn.grid_remove()  # 搜索框有内容前隐藏

        self.count_lbl = ctk.CTkLabel(
            bar, text="", text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 11),
        )
        self.count_lbl.grid(row=0, column=1, padx=(12, 0))

        # 列表卡片
        card = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=12)
        card.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 20))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        # 可排序列头
        col_header = ctk.CTkFrame(card, fg_color="transparent", height=30)
        col_header.grid(row=0, column=0, sticky="ew", padx=2, pady=(8, 0))
        col_header.grid_columnconfigure(1, weight=1)
        col_header.grid_columnconfigure(2, weight=2)

        def make_header(col, sort_key: Optional[SortKey], i18n_key: str, w: int, anchor: str):
            kwargs = {
                "text": t(i18n_key),
                "fg_color": "transparent",
                "hover_color": T.SURFACE_2,
                "text_color": T.TEXT_MUTED,
                "anchor": anchor,
                "font": (T.FONT_FAMILY, 10),
                "height": 24,
                "corner_radius": 6,
            }
            if w:
                kwargs["width"] = w
            if sort_key:
                kwargs["command"] = lambda k=sort_key: self._on_sort_click(k)
                widget = ctk.CTkButton(col_header, **kwargs)
            else:
                # #9：不可排序列使用普通标签，而不是禁用按钮
                widget = ctk.CTkLabel(
                    col_header, text=t(i18n_key),
                    text_color=T.TEXT_MUTED,
                    font=(T.FONT_FAMILY, 10), anchor=anchor,
                )
            padx = (14, 6) if col == 0 else ((6, 14) if col == 4 else 6)
            widget.grid(row=0, column=col, padx=padx, sticky="ew")
            return widget

        # #8：PID 列宽从 70 调整为 80，避免 5-6 位 PID 被截断
        self._header_buttons["pid"] = make_header(0, "pid", "processes.col_pid", 80, "w")
        self._header_buttons["name"] = make_header(1, "name", "processes.col_name", 0, "w")
        self._header_share = make_header(2, None, "processes.col_share", 0, "w")
        self._header_buttons["ws"] = make_header(3, "ws", "processes.col_ws", 110, "e")
        self._header_action = make_header(4, None, "", 80, "e")

        self.scroll = ctk.CTkScrollableFrame(
            card, fg_color="transparent",
            scrollbar_button_color=T.SCROLLBAR,
            scrollbar_button_hover_color=T.SCROLLBAR_HOVER,
        )
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 8))
        self.scroll.grid_columnconfigure(0, weight=1)

        self._rows: List[ProcessRow] = []

        # #10：空状态/加载状态标签
        self._empty_lbl = ctk.CTkLabel(
            self.scroll,
            text=t("processes.loading"),
            text_color=T.TEXT_MUTED,
            font=(T.FONT_FAMILY, 12),
        )
        self._empty_lbl.grid(row=0, column=0, pady=40)

        # #4：键盘导航绑定
        self.scroll.bind("<Up>", self._on_key_up)
        self.scroll.bind("<Down>", self._on_key_down)
        self.scroll.bind("<Return>", self._on_key_enter)
        self.search.bind("<Down>", lambda _e: self._focus_list())

        self._refresh_sort_glyphs()
        self._app.safe_after(200, self._tick)

    # ---- 排除项 --------------------------------------------------------

    @staticmethod
    def _normalize_name(name: str) -> str:
        return name.strip().lower()

    @classmethod
    def _name_aliases(cls, name: str) -> Set[str]:
        key = cls._normalize_name(name)
        if not key:
            return set()
        aliases = {key}
        if key.endswith(".exe"):
            aliases.add(key[:-4])
        else:
            aliases.add(f"{key}.exe")
        return aliases

    @classmethod
    def _excluded_name_set(cls, raw: str) -> Set[str]:
        names: Set[str] = set()
        for part in raw.split(","):
            names.update(cls._name_aliases(part))
        return names

    @staticmethod
    def _format_excluded_names(names: list[str]) -> str:
        return ", ".join(names)

    def _current_excluded_entries(self) -> list[str]:
        seen: Set[str] = set()
        entries: list[str] = []
        for part in self._app.config_obj.excluded_process_names.split(","):
            display = part.strip()
            key = self._normalize_name(display)
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append(display)
        return entries

    def is_process_excluded(self, name: str) -> bool:
        key = self._normalize_name(name)
        excluded = self._excluded_name_set(self._app.config_obj.excluded_process_names)
        return key in excluded

    def set_process_excluded(self, name: str, excluded: bool) -> None:
        key = self._normalize_name(name)
        if not key:
            return

        entries = self._current_excluded_entries()
        target_aliases = self._name_aliases(name)
        filtered = [
            entry for entry in entries
            if self._name_aliases(entry).isdisjoint(target_aliases)
        ]
        if excluded:
            display = name.strip()
            if display and all(self._name_aliases(e).isdisjoint(target_aliases) for e in filtered):
                filtered.append(display)

        self._app.config_obj.excluded_process_names = self._format_excluded_names(filtered)
        self._app.save_config()
        self._sync_settings_exclusions()
        self._render()

    def _sync_settings_exclusions(self) -> None:
        settings = getattr(self._app, "pages", {}).get("settings")
        if settings is not None and hasattr(settings, "_sync_from_config"):
            try:
                settings._sync_from_config()
            except Exception:
                pass

    # ---- 搜索（#3 清除按钮）-------------------------------------------

    def _on_search(self, _evt) -> None:
        self._filter = self.search.get().strip().lower()
        if self._filter:
            self.clear_btn.grid()
        else:
            self.clear_btn.grid_remove()
        self._selected_index = -1
        self._render()

    def _clear_search(self) -> None:
        self.search.delete(0, "end")
        self._filter = ""
        self.clear_btn.grid_remove()
        self._selected_index = -1
        self._render()

    # ---- 键盘导航（#4）-------------------------------------------------

    def _focus_list(self) -> None:
        """将焦点从搜索框移动到可滚动列表。"""
        self.scroll.focus_set()

    def _visible_rows(self) -> list:
        return [r for r in self._rows if r.winfo_ismapped()]

    def _on_key_up(self, _evt) -> None:
        visible = self._visible_rows()
        if not visible:
            return
        if self._selected_index > 0:
            self._selected_index -= 1
        elif self._selected_index < 0:
            self._selected_index = len(visible) - 1
        self._update_selection(visible)

    def _on_key_down(self, _evt) -> None:
        visible = self._visible_rows()
        if not visible:
            return
        if self._selected_index < len(visible) - 1:
            self._selected_index += 1
        self._update_selection(visible)

    def _on_key_enter(self, _evt) -> None:
        visible = self._visible_rows()
        if 0 <= self._selected_index < len(visible):
            visible[self._selected_index]._on_trim()

    def _update_selection(self, visible: list) -> None:
        for i, row in enumerate(visible):
            row.set_selected(i == self._selected_index)

    # ---- 数据 ----------------------------------------------------------

    def _on_sort_click(self, key: SortKey) -> None:
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = key
            self._sort_desc = (key == "ws")
        self._refresh_sort_glyphs()
        self._sort_in_place()
        self._selected_index = -1
        self._render()

    def _refresh_sort_glyphs(self) -> None:
        for k, btn in self._header_buttons.items():
            base = {
                "pid": t("processes.col_pid"),
                "name": t("processes.col_name"),
                "ws": t("processes.col_ws"),
            }[k]
            if k == self._sort_key:
                glyph = self.SORT_GLYPHS["desc" if self._sort_desc else "asc"]
                btn.configure(text=base + glyph, text_color=T.TEXT)
            else:
                btn.configure(text=base, text_color=T.TEXT_MUTED)

    def _sort_in_place(self) -> None:
        key = self._sort_key
        rev = self._sort_desc
        if key == "pid":
            self._procs.sort(key=lambda p: p.get("pid", 0), reverse=rev)
        elif key == "name":
            self._procs.sort(key=lambda p: p.get("name", "").lower(), reverse=rev)
        else:  # 工作集
            self._procs.sort(key=lambda p: p.get("working_set", 0), reverse=rev)

    @staticmethod
    def _normalize_proc(raw: object) -> Optional[dict]:
        """把底层返回的进程数据收敛成渲染层可安全使用的格式。"""
        if not isinstance(raw, dict):
            return None
        try:
            pid = int(raw.get("pid", 0))
            ws = int(raw.get("working_set", 0))
        except (TypeError, ValueError, OverflowError):
            return None
        if pid <= 0:
            return None
        name = raw.get("name", "")
        if not isinstance(name, str):
            name = str(name) if name is not None else ""
        return {
            "pid": pid,
            "name": name.strip() or f"PID {pid}",
            "working_set": max(0, ws),
        }

    def set_total_memory(self, total_bytes: int) -> None:
        total = max(1, int(total_bytes))
        if total == self._total_mem:
            return
        self._total_mem = total
        for row in self._rows:
            if row.winfo_ismapped():
                row.refresh_theme()

    def _tick(self) -> None:
        if getattr(self._app, "_exiting", False):
            return
        if self._active and time.monotonic() - self._last_fetch >= self.REFRESH_SEC:
            self._last_fetch = time.monotonic()
            self._fetch_async()
        self._app.safe_after(1000, self._tick)

    def _fetch_async(self) -> None:
        if not self._fetch_lock.acquire(blocking=False):
            return
        self._fetch_generation += 1
        generation = self._fetch_generation

        def work():
            try:
                procs = list(_core.process_list())
            except Exception:
                try:
                    self._app._logger.debug("process list fetch failed", exc_info=True)
                except Exception:
                    pass
                procs = []
            scheduled = self._app.safe_after(
                0,
                lambda p=procs, gen=generation: self._on_fetched(p, gen),
            )
            if scheduled is None:
                try:
                    self._fetch_lock.release()
                except RuntimeError:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _on_fetched(self, procs: List[dict], generation: int) -> None:
        try:
            if generation != self._fetch_generation:
                return
            normalized = [self._normalize_proc(p) for p in procs]
            self._procs = [p for p in normalized if p is not None]
            self._has_data = True
            self._sort_in_place()
            if self._active:
                self._render()
        finally:
            try:
                self._fetch_lock.release()
            except RuntimeError:
                pass

    def _render(self) -> None:
        filtered = [
            p for p in self._procs
            if not self._filter
            or self._filter in p.get("name", "").lower()
            or (self._filter.isdigit() and self._filter in str(p.get("pid", "")))
        ]
        filtered = filtered[:80]
        self.count_lbl.configure(text=t("processes.count", n=len(filtered)))

        # #10：按需显示空状态
        if not filtered:
            self._empty_lbl.configure(
                text=t("processes.empty") if self._has_data else t("processes.loading"),
            )
            self._empty_lbl.grid()
        else:
            self._empty_lbl.grid_remove()

        share_ref = lambda: self._total_mem

        # #16：基于差异更新，复用行并只更新变化的数据
        while len(self._rows) < len(filtered):
            row = ProcessRow(self.scroll, self._app, share_ref)
            row.grid(row=len(self._rows), column=0, sticky="ew", pady=2)
            self._rows.append(row)

        # 销毁超出缓冲区的多余行，避免无限增长
        max_keep = max(len(filtered) + 10, 30)
        while len(self._rows) > max_keep:
            self._rows.pop().destroy()

        for i, row in enumerate(self._rows):
            if i < len(filtered):
                p = filtered[i]
                excluded = self.is_process_excluded(p.get("name", ""))
                if not row.matches(
                    p["pid"], p.get("name", ""), p["working_set"], self._total_mem, excluded
                ):
                    row.update_proc(p["pid"], p["name"], p["working_set"], excluded)
                row.grid()
            else:
                row.grid_remove()

        # 恢复选择高亮
        visible = self._visible_rows()
        self._update_selection(visible)

    def refresh_theme(self) -> None:
        for row in self._rows:
            row.refresh_theme()

    def refresh_language(self) -> None:
        self.h_title.configure(text=t("processes.title"))
        self.h_subtitle.configure(text=t("processes.subtitle"))
        self.search.configure(placeholder_text=t("processes.search"))
        self._empty_lbl.configure(
            text=t("processes.empty") if self._has_data else t("processes.loading"),
        )
        self._refresh_sort_glyphs()
        # 刷新不可排序列头
        self._header_share.configure(text=t("processes.col_share"))
        for row in self._rows:
            row.refresh_language()
        self._render()

    def on_show(self) -> None:
        self._active = True
        self._last_fetch = 0

    def on_hide(self) -> None:
        self._active = False
