"""Shared scheduling predicates for automatic memory cleanup."""

from __future__ import annotations


def available_floor(total_bytes: int) -> int:
    gib = 1024 * 1024 * 1024
    return max(512 * 1024 * 1024, min(2 * gib, int(total_bytes * 0.08)))


def memory_pressure_high(stats: dict, threshold: int) -> bool:
    percent = float(stats.get("percent", 0.0))
    total = int(stats.get("total", 0) or 0)
    avail = int(stats.get("avail", 0) or 0)
    if total <= 0:
        return percent >= threshold
    return percent >= threshold or avail <= available_floor(total)
