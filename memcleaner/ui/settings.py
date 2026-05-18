"""Settings page — auto cleanup rules + appearance + autostart + about."""

from __future__ import annotations

import customtkinter as ctk

from .. import __version__
from ..autostart import set_autostart as _set_autostart
from .. import theme as T
from ..i18n import t


class Card(ctk.CTkFrame):
    def __init__(self, master, title_key: str) -> None:
        super().__init__(master, fg_color=T.SURFACE, corner_radius=12)
        self._title_key = title_key
        self.title_lbl = ctk.CTkLabel(
            self, text=t(title_key), text_color=T.TEXT,
            font=(T.FONT_FAMILY, 13, "bold"),
        )
        self.title_lbl.pack(anchor="w", padx=20, pady=(14, 2))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=20, pady=(4, 14))

    def refresh_language(self) -> None:
        self.title_lbl.configure(text=t(self._title_key))


class Row(ctk.CTkFrame):
    def __init__(self, master, label_key: str, hint_key: str = "") -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self._label_key = label_key
        self._hint_key = hint_key
        self.label = ctk.CTkLabel(
            self, text=t(label_key), text_color=T.TEXT,
            font=(T.FONT_FAMILY, 12), anchor="w",
        )
        self.label.grid(row=0, column=0, sticky="w")
        self.hint: ctk.CTkLabel | None = None
        if hint_key:
            self.hint = ctk.CTkLabel(
                self, text=t(hint_key), text_color=T.TEXT_MUTED,
                font=(T.FONT_FAMILY, 10), anchor="w",
            )
            self.hint.grid(row=1, column=0, sticky="w", pady=(2, 0))

    def refresh_language(self) -> None:
        self.label.configure(text=t(self._label_key))
        if self.hint is not None and self._hint_key:
            self.hint.configure(text=t(self._hint_key))


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color=T.BG, corner_radius=0)
        self._app = app
        self._cfg = app.config_obj
        self._cards: list[Card] = []
        self._rows: list[Row] = []
        self._threshold_save_after_id: str | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color=T.BG)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        self.h_title = ctk.CTkLabel(
            header, text=t("settings.title"),
            text_color=T.TEXT, font=(T.FONT_FAMILY, 20, "bold"),
        )
        self.h_title.pack(side="left")

        # Body (scrollable)
        self._body = ctk.CTkScrollableFrame(
            self, fg_color=T.BG,
            scrollbar_button_color=T.SCROLLBAR,
            scrollbar_button_hover_color=T.SCROLLBAR_HOVER,
        )
        body = self._body
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        body.grid_columnconfigure(0, weight=1)

        # ---- Card: 阈值 -----------------------------------------------
        card = Card(body, "settings.threshold_card"); self._cards.append(card)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 8))

        r = ctk.CTkFrame(card.body, fg_color="transparent")
        r.pack(fill="x", pady=6); r.grid_columnconfigure(0, weight=1)
        row = Row(r, "settings.threshold_label", "settings.threshold_hint")
        self._rows.append(row); row.grid(row=0, column=0, sticky="ew")
        self.sw_threshold = ctk.CTkSwitch(
            r, text="", command=self._on_threshold_toggle,
            progress_color=T.ACCENT, button_color=T.TEXT,
        )
        self.sw_threshold.grid(row=0, column=1, sticky="e")

        sr = ctk.CTkFrame(card.body, fg_color="transparent")
        sr.pack(fill="x", pady=(6, 0)); sr.grid_columnconfigure(0, weight=1)
        self.threshold_label = ctk.CTkLabel(
            sr, text="", text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 11),
        )
        self.threshold_label.grid(row=0, column=0, sticky="w")
        self.slider = ctk.CTkSlider(
            sr, from_=60, to=95, number_of_steps=35,
            command=self._on_slider, progress_color=T.ACCENT,
            button_color=T.ACCENT, button_hover_color=T.ACCENT_HOVER,
        )
        self.slider.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        seconds_row = ctk.CTkFrame(card.body, fg_color="transparent")
        seconds_row.pack(fill="x", pady=(10, 0))
        seconds_row.grid_columnconfigure(1, weight=1)
        seconds_row.grid_columnconfigure(3, weight=1)
        self.threshold_trigger_lbl = ctk.CTkLabel(
            seconds_row,
            text=t("settings.threshold_trigger_seconds"),
            text_color=T.TEXT_DIM,
            font=(T.FONT_FAMILY, 11),
        )
        self.threshold_trigger_lbl.grid(row=0, column=0, sticky="w")
        self.threshold_trigger_entry = ctk.CTkEntry(
            seconds_row,
            width=74,
            height=28,
            fg_color=T.SURFACE_2,
            border_color=T.SURFACE_3,
            text_color=T.TEXT,
        )
        self.threshold_trigger_entry.grid(row=0, column=1, sticky="w", padx=(8, 18))
        self.threshold_trigger_entry.bind("<FocusOut>", self._on_threshold_trigger_value)
        self.threshold_trigger_entry.bind("<Return>", self._on_threshold_trigger_value)
        self.threshold_trigger_entry.bind(
            "<KeyRelease>",
            lambda _e: self._validate_int_entry(self.threshold_trigger_entry, 0, 300),
        )

        self.threshold_cooldown_lbl = ctk.CTkLabel(
            seconds_row,
            text=t("settings.threshold_seconds"),
            text_color=T.TEXT_DIM,
            font=(T.FONT_FAMILY, 11),
        )
        self.threshold_cooldown_lbl.grid(row=0, column=2, sticky="w")
        self.threshold_cooldown_entry = ctk.CTkEntry(
            seconds_row,
            width=74,
            height=28,
            fg_color=T.SURFACE_2,
            border_color=T.SURFACE_3,
            text_color=T.TEXT,
        )
        self.threshold_cooldown_entry.grid(row=0, column=3, sticky="w", padx=(8, 0))
        self.threshold_cooldown_entry.bind("<FocusOut>", self._on_threshold_cooldown_value)
        self.threshold_cooldown_entry.bind("<Return>", self._on_threshold_cooldown_value)
        self.threshold_cooldown_entry.bind(
            "<KeyRelease>",
            lambda _e: self._validate_int_entry(self.threshold_cooldown_entry, 5, 3600),
        )

        # ---- Card: 定时 -----------------------------------------------
        card2 = Card(body, "settings.interval_card"); self._cards.append(card2)
        card2.grid(row=1, column=0, sticky="ew", padx=4, pady=8)
        r = ctk.CTkFrame(card2.body, fg_color="transparent")
        r.pack(fill="x", pady=6); r.grid_columnconfigure(0, weight=1)
        row = Row(r, "settings.interval_label"); self._rows.append(row)
        row.grid(row=0, column=0, sticky="ew")
        self.sw_interval = ctk.CTkSwitch(
            r, text="", command=self._on_interval_toggle,
            progress_color=T.ACCENT, button_color=T.TEXT,
        )
        self.sw_interval.grid(row=0, column=1, sticky="e")

        ir = ctk.CTkFrame(card2.body, fg_color="transparent")
        ir.pack(fill="x", pady=(6, 0))
        self.interval_lbl = ctk.CTkLabel(
            ir, text=t("settings.interval_minutes"),
            text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 11),
        )
        self.interval_lbl.pack(side="left")
        self.interval_entry = ctk.CTkEntry(
            ir, width=80, height=28,
            fg_color=T.SURFACE_2, border_color=T.SURFACE_3,
            text_color=T.TEXT,
        )
        self.interval_entry.pack(side="left", padx=(8, 0))
        self.interval_entry.bind("<FocusOut>", self._on_interval_value)
        self.interval_entry.bind("<Return>", self._on_interval_value)
        self.interval_entry.bind("<KeyRelease>", self._on_interval_keyrelease)

        # ---- Card: 外观 -----------------------------------------------
        card3 = Card(body, "settings.appearance_card"); self._cards.append(card3)
        card3.grid(row=2, column=0, sticky="ew", padx=4, pady=8)

        ar = ctk.CTkFrame(card3.body, fg_color="transparent")
        ar.pack(fill="x", pady=6); ar.grid_columnconfigure(0, weight=1)
        row = Row(ar, "settings.theme_label"); self._rows.append(row)
        row.grid(row=0, column=0, sticky="w")
        # #15: fixed width to prevent size jump on language change
        self.theme_seg = ctk.CTkSegmentedButton(
            ar,
            values=[t("settings.theme_dark"), t("settings.theme_light")],
            command=self._on_theme_change,
            width=160,
            fg_color=T.SURFACE_2,
            selected_color=T.ACCENT,
            selected_hover_color=T.ACCENT_HOVER,
            unselected_color=T.SURFACE_2,
            unselected_hover_color=T.SURFACE_3,
            text_color=T.TEXT,
            font=(T.FONT_FAMILY, 11),
        )
        self.theme_seg.grid(row=0, column=1, sticky="e")

        lr = ctk.CTkFrame(card3.body, fg_color="transparent")
        lr.pack(fill="x", pady=(10, 0)); lr.grid_columnconfigure(0, weight=1)
        row = Row(lr, "settings.language_label"); self._rows.append(row)
        row.grid(row=0, column=0, sticky="w")
        # #15: fixed width
        self.lang_seg = ctk.CTkSegmentedButton(
            lr,
            values=[t("settings.language_zh"), t("settings.language_en")],
            command=self._on_language_change,
            width=160,
            fg_color=T.SURFACE_2,
            selected_color=T.ACCENT,
            selected_hover_color=T.ACCENT_HOVER,
            unselected_color=T.SURFACE_2,
            unselected_hover_color=T.SURFACE_3,
            text_color=T.TEXT,
            font=(T.FONT_FAMILY, 11),
        )
        self.lang_seg.grid(row=0, column=1, sticky="e")

        # ---- Card: 其他 -----------------------------------------------
        card4 = Card(body, "settings.other_card"); self._cards.append(card4)
        card4.grid(row=3, column=0, sticky="ew", padx=4, pady=8)

        rm = ctk.CTkFrame(card4.body, fg_color="transparent")
        rm.pack(fill="x", pady=6); rm.grid_columnconfigure(0, weight=1)
        row = Row(rm, "settings.cleaning_mode_label", "settings.cleaning_mode_hint")
        self._rows.append(row); row.grid(row=0, column=0, sticky="ew")
        self.cleaning_mode_seg = ctk.CTkSegmentedButton(
            rm,
            values=[
                t("settings.cleaning_mode_conservative"),
                t("settings.cleaning_mode_balanced"),
                t("settings.cleaning_mode_aggressive"),
            ],
            command=self._on_cleaning_mode_change,
            width=240,
            fg_color=T.SURFACE_2,
            selected_color=T.ACCENT,
            selected_hover_color=T.ACCENT_HOVER,
            unselected_color=T.SURFACE_2,
            unselected_hover_color=T.SURFACE_3,
            text_color=T.TEXT,
            font=(T.FONT_FAMILY, 11),
        )
        self.cleaning_mode_seg.grid(row=0, column=1, sticky="e")

        self.cleaning_mode_desc = ctk.CTkLabel(
            card4.body,
            text="",
            text_color=T.TEXT_DIM,
            font=(T.FONT_FAMILY, 10),
            justify="left",
            wraplength=520,
            anchor="w",
        )
        self.cleaning_mode_desc.pack(fill="x", pady=(0, 6))

        self.cleaning_strategy_frame = ctk.CTkFrame(card4.body, fg_color="transparent")
        self.cleaning_strategy_frame.pack(fill="x", pady=(0, 8))
        self.cleaning_strategy_frame.grid_columnconfigure(1, weight=1)
        self._strategy_labels: dict[str, tuple[ctk.CTkLabel, ctk.CTkLabel]] = {}
        for idx, key in enumerate((
            "min_ws",
            "system_ws",
            "file_cache",
            "modified",
            "standby",
            "protection",
        )):
            label = ctk.CTkLabel(
                self.cleaning_strategy_frame,
                text="",
                text_color=T.TEXT_MUTED,
                font=(T.FONT_FAMILY, 10),
                anchor="w",
            )
            label.grid(row=idx, column=0, sticky="w", pady=1)
            value = ctk.CTkLabel(
                self.cleaning_strategy_frame,
                text="",
                text_color=T.TEXT_DIM,
                font=(T.FONT_FAMILY, 10),
                anchor="w",
                justify="left",
            )
            value.grid(row=idx, column=1, sticky="w", padx=(12, 0), pady=1)
            self._strategy_labels[key] = (label, value)

        r = ctk.CTkFrame(card4.body, fg_color="transparent")
        r.pack(fill="x", pady=6); r.grid_columnconfigure(0, weight=1)
        row = Row(r, "settings.standby_label", "settings.standby_hint")
        self._rows.append(row); row.grid(row=0, column=0, sticky="ew")
        self.sw_standby = ctk.CTkSwitch(
            r, text="", command=self._on_standby_toggle,
            progress_color=T.ACCENT, button_color=T.TEXT,
        )
        self.sw_standby.grid(row=0, column=1, sticky="e")
        if not self._app._is_admin():
            self.sw_standby.configure(state="disabled")

        r1 = ctk.CTkFrame(card4.body, fg_color="transparent")
        r1.pack(fill="x", pady=(10, 6)); r1.grid_columnconfigure(0, weight=1)
        row = Row(r1, "settings.exclude_foreground_label", "settings.exclude_foreground_hint")
        self._rows.append(row); row.grid(row=0, column=0, sticky="ew")
        self.sw_exclude_foreground = ctk.CTkSwitch(
            r1, text="", command=self._on_exclude_foreground_toggle,
            progress_color=T.ACCENT, button_color=T.TEXT,
        )
        self.sw_exclude_foreground.grid(row=0, column=1, sticky="e")

        r1b = ctk.CTkFrame(card4.body, fg_color="transparent")
        r1b.pack(fill="x", pady=(0, 6)); r1b.grid_columnconfigure(0, weight=1)
        row = Row(r1b, "settings.exclude_names_label", "settings.exclude_names_hint")
        self._rows.append(row); row.grid(row=0, column=0, sticky="ew")
        self.excluded_names_entry = ctk.CTkEntry(
            r1b,
            width=220,
            height=28,
            fg_color=T.SURFACE_2,
            border_color=T.SURFACE_3,
            text_color=T.TEXT,
        )
        self.excluded_names_entry.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.excluded_names_entry.bind("<FocusOut>", self._on_excluded_names_value)
        self.excluded_names_entry.bind("<Return>", self._on_excluded_names_value)

        r2 = ctk.CTkFrame(card4.body, fg_color="transparent")
        r2.pack(fill="x", pady=(10, 6)); r2.grid_columnconfigure(0, weight=1)
        row = Row(r2, "settings.tray_label", "settings.tray_hint")
        self._rows.append(row); row.grid(row=0, column=0, sticky="ew")
        self.sw_tray = ctk.CTkSwitch(
            r2, text="", command=self._on_tray_toggle,
            progress_color=T.ACCENT, button_color=T.TEXT,
        )
        self.sw_tray.grid(row=0, column=1, sticky="e")

        r3 = ctk.CTkFrame(card4.body, fg_color="transparent")
        r3.pack(fill="x", pady=(10, 6)); r3.grid_columnconfigure(0, weight=1)
        row = Row(r3, "settings.background_mode_label", "settings.background_mode_hint")
        self._rows.append(row); row.grid(row=0, column=0, sticky="ew")
        self.mode_seg = ctk.CTkSegmentedButton(
            r3,
            values=[t("settings.background_mode_light"), t("settings.background_mode_full")],
            command=self._on_background_mode_change,
            width=160,
            fg_color=T.SURFACE_2,
            selected_color=T.ACCENT,
            selected_hover_color=T.ACCENT_HOVER,
            unselected_color=T.SURFACE_2,
            unselected_hover_color=T.SURFACE_3,
            text_color=T.TEXT,
            font=(T.FONT_FAMILY, 11),
        )
        self.mode_seg.grid(row=0, column=1, sticky="e")

        r4 = ctk.CTkFrame(card4.body, fg_color="transparent")
        r4.pack(fill="x", pady=(10, 6)); r4.grid_columnconfigure(0, weight=1)
        row = Row(r4, "settings.autostart_label", "settings.autostart_hint")
        self._rows.append(row); row.grid(row=0, column=0, sticky="ew")
        self.sw_autostart = ctk.CTkSwitch(
            r4, text="", command=self._on_autostart_toggle,
            progress_color=T.ACCENT, button_color=T.TEXT,
        )
        self.sw_autostart.grid(row=0, column=1, sticky="e")

        r5 = ctk.CTkFrame(card4.body, fg_color="transparent")
        r5.pack(fill="x", pady=(10, 6)); r5.grid_columnconfigure(0, weight=1)
        row = Row(r5, "settings.auto_elevate_label", "settings.auto_elevate_hint")
        self._rows.append(row); row.grid(row=0, column=0, sticky="ew")
        self.sw_auto_elevate = ctk.CTkSwitch(
            r5, text="", command=self._on_auto_elevate_toggle,
            progress_color=T.ACCENT, button_color=T.TEXT,
        )
        self.sw_auto_elevate.grid(row=0, column=1, sticky="e")

        # ---- About -----------------------------------------------------
        about = Card(body, "settings.about_card"); self._cards.append(about)
        about.grid(row=4, column=0, sticky="ew", padx=4, pady=(10, 4))
        self.about_text = ctk.CTkLabel(
            about.body,
            text=t("settings.about_text", version=__version__),
            text_color=T.TEXT_DIM, font=(T.FONT_FAMILY, 11),
            justify="left",
        )
        self.about_text.pack(anchor="w")

        self._sync_from_config()

    # ---- handlers ------------------------------------------------------

    def _sync_from_config(self) -> None:
        c = self._cfg
        (self.sw_threshold.select if c.threshold_enabled else self.sw_threshold.deselect)()
        self.slider.set(c.threshold_percent)
        self._update_threshold_label()
        self.threshold_trigger_entry.delete(0, "end")
        self.threshold_trigger_entry.insert(0, str(c.threshold_trigger_seconds))
        self.threshold_cooldown_entry.delete(0, "end")
        self.threshold_cooldown_entry.insert(0, str(c.threshold_cooldown_seconds))

        (self.sw_interval.select if c.interval_enabled else self.sw_interval.deselect)()
        self.interval_entry.delete(0, "end")
        self.interval_entry.insert(0, str(c.interval_minutes))

        (self.sw_exclude_foreground.select if c.exclude_foreground_process else self.sw_exclude_foreground.deselect)()
        self.excluded_names_entry.delete(0, "end")
        self.excluded_names_entry.insert(0, c.excluded_process_names)
        (self.sw_tray.select if c.tray_enabled else self.sw_tray.deselect)()
        (self.sw_autostart.select if c.autostart else self.sw_autostart.deselect)()
        (self.sw_auto_elevate.select if c.auto_elevate else self.sw_auto_elevate.deselect)()

        # Disconnect callbacks before .set() to prevent re-entrancy
        self.cleaning_mode_seg.configure(command=None)
        self.theme_seg.configure(command=None)
        self.lang_seg.configure(command=None)
        self.mode_seg.configure(command=None)

        self.cleaning_mode_seg.set(
            t(f"settings.cleaning_mode_{c.cleaning_mode}")
        )

        self.theme_seg.set(
            t("settings.theme_dark" if c.theme == "dark" else "settings.theme_light")
        )
        self.lang_seg.set(
            t("settings.language_zh" if c.language == "zh" else "settings.language_en")
        )
        self.mode_seg.set(
            t(
                "settings.background_mode_light"
                if c.background_mode == "light"
                else "settings.background_mode_full"
            )
        )

        # Reconnect callbacks
        self.cleaning_mode_seg.configure(command=self._on_cleaning_mode_change)
        self.theme_seg.configure(command=self._on_theme_change)
        self.lang_seg.configure(command=self._on_language_change)
        self.mode_seg.configure(command=self._on_background_mode_change)
        self._refresh_cleaning_mode_ui()

    def _update_threshold_label(self) -> None:
        self.threshold_label.configure(
            text=t("settings.threshold_value", percent=self._cfg.threshold_percent)
        )

    def _on_interval_keyrelease(self, _evt=None) -> None:
        self._validate_int_entry(self.interval_entry, 1, 1440)

    def _validate_int_entry(self, entry: ctk.CTkEntry, low: int, high: int) -> None:
        text = entry.get().strip()
        if not text:
            entry.configure(border_color=T.SURFACE_3)
            return
        try:
            v = int(text)
            valid = low <= v <= high
        except ValueError:
            valid = False
        entry.configure(border_color=T.SURFACE_3 if valid else T.DANGER)

    def _on_threshold_toggle(self) -> None:
        self._cfg.threshold_enabled = bool(self.sw_threshold.get())
        self._app.save_config()

    def _on_slider(self, value) -> None:
        self._cfg.threshold_percent = int(round(float(value)))
        self._update_threshold_label()
        self._save_config_debounced()

    def _on_threshold_trigger_value(self, _evt=None) -> None:
        try:
            value = int(self.threshold_trigger_entry.get().strip())
        except ValueError:
            value = self._cfg.threshold_trigger_seconds
        value = max(0, min(300, value))
        self._cfg.threshold_trigger_seconds = value
        self.threshold_trigger_entry.delete(0, "end")
        self.threshold_trigger_entry.insert(0, str(value))
        self.threshold_trigger_entry.configure(border_color=T.SURFACE_3)
        self._app.save_config()

    def _on_threshold_cooldown_value(self, _evt=None) -> None:
        try:
            value = int(self.threshold_cooldown_entry.get().strip())
        except ValueError:
            value = self._cfg.threshold_cooldown_seconds
        value = max(5, min(3600, value))
        self._cfg.threshold_cooldown_seconds = value
        self.threshold_cooldown_entry.delete(0, "end")
        self.threshold_cooldown_entry.insert(0, str(value))
        self.threshold_cooldown_entry.configure(border_color=T.SURFACE_3)
        self._app.save_config()

    def _save_config_debounced(self) -> None:
        if self._threshold_save_after_id is not None:
            try:
                self.after_cancel(self._threshold_save_after_id)
            except Exception:
                pass
        self._threshold_save_after_id = self.after(350, self._flush_debounced_config)

    def _flush_debounced_config(self) -> None:
        self._threshold_save_after_id = None
        self._app.save_config()

    def flush_pending_config(self) -> None:
        if self._threshold_save_after_id is None:
            return
        try:
            self.after_cancel(self._threshold_save_after_id)
        except Exception:
            pass
        self._flush_debounced_config()

    def _on_interval_toggle(self) -> None:
        self._cfg.interval_enabled = bool(self.sw_interval.get())
        self._app.save_config()

    def _on_interval_value(self, _evt=None) -> None:
        try:
            v = int(self.interval_entry.get().strip())
        except ValueError:
            v = self._cfg.interval_minutes
        v = max(1, min(1440, v))
        self._cfg.interval_minutes = v
        self.interval_entry.delete(0, "end")
        self.interval_entry.insert(0, str(v))
        self._app.save_config()

    def _cleaning_mode_desc_key(self, mode: str) -> str:
        return f"settings.cleaning_mode_desc_{mode}"

    def _cleaning_mode_strategy(self, mode: str) -> dict[str, str]:
        if mode == "conservative":
            return {
                "min_ws": "512 MB",
                "system_ws": t("settings.strategy_no"),
                "file_cache": t("settings.strategy_no"),
                "modified": t("settings.strategy_no"),
                "standby": t("settings.strategy_by_switch"),
                "protection": t("settings.strategy_protection_extended"),
            }
        if mode == "aggressive":
            return {
                "min_ws": "64 MB",
                "system_ws": t("settings.strategy_yes"),
                "file_cache": t("settings.strategy_yes"),
                "modified": t("settings.strategy_yes"),
                "standby": t("settings.strategy_always"),
                "protection": t("settings.strategy_protection_critical"),
            }
        return {
            "min_ws": "192 MB",
            "system_ws": t("settings.strategy_yes"),
            "file_cache": t("settings.strategy_yes"),
            "modified": t("settings.strategy_no"),
            "standby": t("settings.strategy_by_switch"),
            "protection": t("settings.strategy_protection_extended"),
        }

    def _refresh_cleaning_mode_ui(self) -> None:
        mode = self._cfg.cleaning_mode
        self.cleaning_mode_desc.configure(text=t(self._cleaning_mode_desc_key(mode)))
        strategy = self._cleaning_mode_strategy(mode)
        for key, (label, value) in self._strategy_labels.items():
            label.configure(text=t(f"settings.strategy_{key}"))
            value.configure(text=strategy[key])
        standby_enabled = self._cfg.effective_clear_standby_too()
        (self.sw_standby.select if standby_enabled else self.sw_standby.deselect)()
        if mode == "aggressive" or not self._app._is_admin():
            self.sw_standby.configure(state="disabled")
        else:
            self.sw_standby.configure(state="normal")

    def _on_cleaning_mode_change(self, value: str) -> None:
        vals = list(self.cleaning_mode_seg.cget("values"))
        if vals and value == vals[0]:
            new_mode = "conservative"
        elif len(vals) > 2 and value == vals[2]:
            new_mode = "aggressive"
        else:
            new_mode = "balanced"
        if new_mode == self._cfg.cleaning_mode:
            return
        self._cfg.cleaning_mode = new_mode
        self._refresh_cleaning_mode_ui()
        self._app.save_config()

    def _on_exclude_foreground_toggle(self) -> None:
        self._cfg.exclude_foreground_process = bool(self.sw_exclude_foreground.get())
        self._app.save_config()

    def _on_excluded_names_value(self, _evt=None) -> None:
        raw = self.excluded_names_entry.get().strip()
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        normalized = ", ".join(parts)
        self._cfg.excluded_process_names = normalized
        self.excluded_names_entry.delete(0, "end")
        self.excluded_names_entry.insert(0, normalized)
        self._app.save_config()

    def _on_standby_toggle(self) -> None:
        if self._cfg.cleaning_mode == "aggressive" or not self._app._is_admin():
            self._sync_from_config()
            return
        self._cfg.clear_standby_too = bool(self.sw_standby.get())
        self._app.save_config()

    def _on_tray_toggle(self) -> None:
        desired = bool(self.sw_tray.get())
        self._cfg.tray_enabled = desired
        self._app.save_config()
        ok = self._app.apply_tray_setting()
        if desired and not ok:
            self.sw_tray.deselect()

    def _on_background_mode_change(self, value: str) -> None:
        vals = list(self.mode_seg.cget("values"))
        new_mode = "light" if vals and value == vals[0] else "full"
        if new_mode == self._cfg.background_mode:
            return
        if new_mode == "light" and self._app._find_rust_daemon() is None:
            self._app.show_toast(t("toast.background_mode_target_missing"), "error")
            self._sync_from_config()
            return
        self._cfg.background_mode = new_mode
        self._app.save_config()
        if self._cfg.autostart:
            command = self._app.build_autostart_command()
            if command is None:
                self._app.show_toast(t("toast.autostart_target_missing"), "error")
            elif not _set_autostart(True, command):
                self._app.show_toast(t("toast.registry_failed"), "error")
        self._app.apply_tray_setting()

    def _on_autostart_toggle(self) -> None:
        desired = bool(self.sw_autostart.get())
        command = self._app.build_autostart_command() if desired else None
        if desired and command is None:
            self._app.show_toast(t("toast.autostart_target_missing"), "error")
            self.sw_autostart.deselect()
            return
        ok = _set_autostart(desired, command)
        if not ok:
            self._app.show_toast(t("toast.registry_failed"), "error")
            (self.sw_autostart.deselect if desired else self.sw_autostart.select)()
            return
        self._cfg.autostart = desired
        self._app.save_config()

    def _on_auto_elevate_toggle(self) -> None:
        desired = bool(self.sw_auto_elevate.get())
        self._cfg.auto_elevate = desired
        self._app.save_config()
        if desired and not self._app._is_admin():
            self._app.show_toast(t("toast.auto_elevate_next_start"), "info")

    def _on_theme_change(self, value: str) -> None:
        # Use config value instead of display string to avoid i18n mismatch
        vals = list(self.theme_seg.cget("values"))
        new_theme = "dark" if vals and value == vals[0] else "light"
        if new_theme == self._cfg.theme:
            return
        self._cfg.theme = new_theme
        self._app.save_config()
        self._app.apply_theme(new_theme)

    def _on_language_change(self, value: str) -> None:
        vals = list(self.lang_seg.cget("values"))
        new_lang = "zh" if vals and value == vals[0] else "en"
        if new_lang == self._cfg.language:
            return
        self._cfg.language = new_lang
        self._app.save_config()
        self._app.apply_language(new_lang)

    # ---- public --------------------------------------------------------

    def refresh_theme(self) -> None:
        self.configure(fg_color=T.BG)
        self.h_title.configure(text_color=T.TEXT)
        for card in self._cards:
            card.configure(fg_color=T.SURFACE)
            card.title_lbl.configure(text_color=T.TEXT)
        for row in self._rows:
            row.label.configure(text_color=T.TEXT)
            if row.hint is not None:
                row.hint.configure(text_color=T.TEXT_MUTED)
        self.threshold_label.configure(text_color=T.TEXT_DIM)
        self.threshold_trigger_lbl.configure(text_color=T.TEXT_DIM)
        self.threshold_cooldown_lbl.configure(text_color=T.TEXT_DIM)
        for entry in (self.threshold_trigger_entry, self.threshold_cooldown_entry):
            entry.configure(
                fg_color=T.SURFACE_2,
                border_color=T.SURFACE_3,
                text_color=T.TEXT,
            )
        self.interval_lbl.configure(text_color=T.TEXT_DIM)
        self.cleaning_mode_desc.configure(text_color=T.TEXT_DIM)
        for label, value in self._strategy_labels.values():
            label.configure(text_color=T.TEXT_MUTED)
            value.configure(text_color=T.TEXT_DIM)
        self.interval_entry.configure(
            fg_color=T.SURFACE_2,
            border_color=T.SURFACE_3,
            text_color=T.TEXT,
        )
        self.excluded_names_entry.configure(
            fg_color=T.SURFACE_2,
            border_color=T.SURFACE_3,
            text_color=T.TEXT,
        )
        self.slider.configure(
            progress_color=T.ACCENT,
            button_color=T.ACCENT,
            button_hover_color=T.ACCENT_HOVER,
        )
        for switch in (
            self.sw_threshold,
            self.sw_interval,
            self.sw_standby,
            self.sw_exclude_foreground,
            self.sw_tray,
            self.sw_autostart,
            self.sw_auto_elevate,
        ):
            switch.configure(progress_color=T.ACCENT, button_color=T.TEXT)
        for seg in (self.cleaning_mode_seg, self.theme_seg, self.lang_seg, self.mode_seg):
            seg.configure(
                fg_color=T.SURFACE_2,
                selected_color=T.ACCENT,
                selected_hover_color=T.ACCENT_HOVER,
                unselected_color=T.SURFACE_2,
                unselected_hover_color=T.SURFACE_3,
                text_color=T.TEXT,
            )
        self.about_text.configure(text_color=T.TEXT_DIM)
        self._body.configure(
            fg_color=T.BG,
            scrollbar_button_color=T.SCROLLBAR,
            scrollbar_button_hover_color=T.SCROLLBAR_HOVER,
        )

    def refresh_language(self) -> None:
        self.h_title.configure(text=t("settings.title"))
        for c in self._cards:
            c.refresh_language()
        for r in self._rows:
            r.refresh_language()
        self.interval_lbl.configure(text=t("settings.interval_minutes"))
        self.threshold_trigger_lbl.configure(text=t("settings.threshold_trigger_seconds"))
        self.threshold_cooldown_lbl.configure(text=t("settings.threshold_seconds"))
        self._update_threshold_label()
        self.cleaning_mode_seg.configure(
            values=[
                t("settings.cleaning_mode_conservative"),
                t("settings.cleaning_mode_balanced"),
                t("settings.cleaning_mode_aggressive"),
            ]
        )
        self.theme_seg.configure(
            values=[t("settings.theme_dark"), t("settings.theme_light")]
        )
        self.lang_seg.configure(
            values=[t("settings.language_zh"), t("settings.language_en")]
        )
        self.mode_seg.configure(
            values=[t("settings.background_mode_light"), t("settings.background_mode_full")]
        )
        self.about_text.configure(
            text=t("settings.about_text", version=__version__)
        )
        self._sync_from_config()

    def on_show(self) -> None:
        self._sync_from_config()
