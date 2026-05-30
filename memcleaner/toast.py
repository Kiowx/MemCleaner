"""In-app toast notifications."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from . import theme as T


class _ToastManager:
    """Track active toast y positions so new toasts can stack cleanly."""

    def __init__(self) -> None:
        self._active: list[int] = []

    def push(self, y: int) -> None:
        self._active.append(y)

    def pop(self, y: int) -> None:
        try:
            self._active.remove(y)
        except ValueError:
            pass

    def next_y(self, parent_h: int, toast_h: int, base_y: int = 20) -> int:
        base = base_y
        for existing_y in self._active:
            if abs(base - existing_y) < toast_h + 8:
                base = existing_y + toast_h + 8
        return max(12, min(base, max(12, parent_h - toast_h - 20)))


_toast_mgr = _ToastManager()


class Toast(ctk.CTkFrame):
    """Auto-dismiss notification that stacks with sibling toasts."""

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

        dot = ctk.CTkFrame(row, fg_color=dot_color, width=8, height=8, corner_radius=4)
        dot.pack(side="left", padx=(0, 10), pady=2)
        ctk.CTkLabel(
            row,
            text=text_,
            text_color=T.TEXT,
            font=(T.FONT_FAMILY, 12),
            wraplength=max(220, min(520, parent.winfo_width() - 96)),
            justify="left",
        ).pack(side="left")

        for widget in (self, row, dot):
            widget.bind("<Button-1>", lambda _e: self._destroy())
        for child in row.winfo_children():
            child.bind("<Button-1>", lambda _e: self._destroy())

        self.update_idletasks()
        height = self.winfo_height()
        parent_height = parent.winfo_height()
        y_rel = _toast_mgr.next_y(parent_height, height, base_y=max(48, int(parent_height * 0.05)))
        _toast_mgr.push(y_rel)
        self._toast_y = y_rel
        self.place(relx=0.5, y=y_rel, anchor="n")
        self.lift()
        self._schedule_destroy(kind)

    def _schedule_destroy(self, kind: str) -> None:
        self.after(self._DURATIONS.get(kind, 2400), self._destroy)

    def _destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        _toast_mgr.pop(self._toast_y)
        try:
            self.destroy()
        except tk.TclError:
            pass
