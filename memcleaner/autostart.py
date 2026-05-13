from __future__ import annotations


REGISTRY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_VALUE_NAME = "MemCleaner"


def set_autostart(enabled: bool, command: str | None = None) -> bool:
    try:
        import winreg  # type: ignore
    except ImportError:
        return False
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, REGISTRY_RUN_KEY, 0, winreg.KEY_SET_VALUE
        )
    except OSError:
        return False
    try:
        if enabled:
            if not command:
                return False
            winreg.SetValueEx(key, REGISTRY_VALUE_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, REGISTRY_VALUE_NAME)
            except FileNotFoundError:
                pass
        return True
    except OSError:
        return False
    finally:
        winreg.CloseKey(key)


def get_autostart_command() -> str | None:
    try:
        import winreg  # type: ignore
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, REGISTRY_VALUE_NAME)
    except OSError:
        return None
    return value if isinstance(value, str) else None
