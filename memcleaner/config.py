"""持久化用户设置，以 JSON 形式存放在 %APPDATA%\\memcleaner。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / "memcleaner"
    p.mkdir(parents=True, exist_ok=True)
    return p


CONFIG_PATH = _config_dir() / "config.json"


@dataclass
class Config:
    threshold_enabled: bool = False
    threshold_percent: int = 80
    threshold_trigger_seconds: int = 5
    threshold_cooldown_seconds: int = 60
    interval_enabled: bool = False
    interval_minutes: int = 30
    autostart: bool = False
    auto_elevate: bool = False
    clear_standby_too: bool = False
    cleaning_mode: str = "balanced"  # "conservative" | "balanced" | "aggressive"
    exclude_foreground_process: bool = True
    excluded_process_names: str = ""
    tray_enabled: bool = False
    background_mode: str = "light"  # "light" 关闭 GUI 并交给 Rust 守护进程；"full" 保留 Python GUI 后台驻留
    gui_exe_path: str = ""         # 打包版 GUI exe 路径，供轻量后台守护进程重新打开窗口
    theme: str = "dark"          # "dark" | "light"
    language: str = "zh"         # "zh" | "en"
    window_geometry: str = ""    # 跨会话持久化的 "WxH+X+Y"

    def effective_clear_standby_too(self) -> bool:
        return self.clear_standby_too or self.cleaning_mode == "aggressive"

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        cfg = cls()

        def bool_value(key: str, default: bool) -> bool:
            value = raw.get(key, default)
            return value if isinstance(value, bool) else default

        def int_value(key: str, default: int, low: int, high: int) -> int:
            value = raw.get(key, default)
            if isinstance(value, bool):
                return default
            try:
                value = int(value)
            except (TypeError, ValueError):
                return default
            return max(low, min(high, value))

        cfg.threshold_enabled = bool_value("threshold_enabled", cfg.threshold_enabled)
        cfg.threshold_percent = int_value("threshold_percent", cfg.threshold_percent, 1, 99)
        cfg.threshold_trigger_seconds = int_value(
            "threshold_trigger_seconds",
            cfg.threshold_trigger_seconds,
            0,
            300,
        )
        cfg.threshold_cooldown_seconds = int_value(
            "threshold_cooldown_seconds",
            cfg.threshold_cooldown_seconds,
            5,
            3600,
        )
        cfg.interval_enabled = bool_value("interval_enabled", cfg.interval_enabled)
        cfg.interval_minutes = int_value("interval_minutes", cfg.interval_minutes, 1, 1440)
        cfg.autostart = bool_value("autostart", cfg.autostart)
        cfg.auto_elevate = bool_value("auto_elevate", cfg.auto_elevate)
        cfg.clear_standby_too = bool_value("clear_standby_too", cfg.clear_standby_too)
        cleaning_mode = raw.get("cleaning_mode", cfg.cleaning_mode)
        cfg.cleaning_mode = (
            cleaning_mode
            if cleaning_mode in ("conservative", "balanced", "aggressive")
            else "balanced"
        )
        if cfg.cleaning_mode == "aggressive":
            cfg.clear_standby_too = True
        cfg.tray_enabled = bool_value("tray_enabled", cfg.tray_enabled)
        mode = raw.get("background_mode", cfg.background_mode)
        cfg.background_mode = mode if mode in ("light", "full") else "light"
        gui_exe_path = raw.get("gui_exe_path", cfg.gui_exe_path)
        cfg.gui_exe_path = gui_exe_path if isinstance(gui_exe_path, str) else ""
        cfg.exclude_foreground_process = bool_value(
            "exclude_foreground_process", cfg.exclude_foreground_process
        )
        excluded = raw.get("excluded_process_names", cfg.excluded_process_names)
        cfg.excluded_process_names = excluded if isinstance(excluded, str) else ""

        theme = raw.get("theme", cfg.theme)
        cfg.theme = theme if theme in ("dark", "light") else "dark"
        language = raw.get("language", cfg.language)
        cfg.language = language if language in ("zh", "en") else "zh"

        geom = raw.get("window_geometry", "")
        if isinstance(geom, str) and geom:
            # 校验几何格式：WxH+X+Y（负偏移时为 WxH+-X+-Y）
            if re.match(r'^\d+x\d+[+-]\d+[+-]\d+$', geom):
                cfg.window_geometry = geom
        return cfg

    def save(self) -> None:
        """原子化保存配置：先写入临时文件，再替换目标文件。"""
        tmp = CONFIG_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(str(tmp), str(CONFIG_PATH))
        except OSError:
            # 如果替换失败，则回退为直接写入
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            CONFIG_PATH.write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
