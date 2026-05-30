"""MemCleaner — 现代 Windows 内存清理工具。"""

from __future__ import annotations

from typing import Optional

__version__ = "0.3.5"

_core = None
_core_error: Optional[ImportError] = None
del _core  # 移除占位对象，确保下面的相对导入解析为子模块

try:
    from . import _core  # type: ignore
except ImportError as e:  # pragma: no cover
    _core = None
    _core_error = e

__all__ = ["_core", "_core_error", "__version__"]
