"""自动清理调度器：阈值触发与定时触发。"""

from __future__ import annotations

import queue
import threading
import time
from typing import List, Optional

from . import _core
from .config import Config
from .monitor import Monitor
from .scheduling import available_floor, memory_pressure_high



class Scheduler:
    """轮询监控器和配置，在满足条件时触发清理。

    冷却时间用于控制内存持续超过阈值时的重复触发。
    结果会进入队列，必须在主线程通过 :meth:`drain_results` 消费，
    以避免跨线程访问 Tk。
    """

    DEFAULT_THRESHOLD_COOLDOWN_SECONDS = 60
    LOW_YIELD_FREED_MB = 64.0
    LOW_YIELD_COOLDOWN_MULTIPLIER = 3

    def __init__(
        self,
        monitor: Monitor,
        config: Config,
        trim_lock: Optional[threading.Lock] = None,
        **_kw,
    ) -> None:
        self._monitor = monitor
        self._config = config
        self._trim_lock = trim_lock or threading.Lock()
        self._stop = threading.Event()
        self._last_clean = time.monotonic()
        self._last_threshold_clean = 0.0
        self._last_threshold_percent = int(config.threshold_percent)
        self._last_threshold_trigger_seconds = int(config.threshold_trigger_seconds)
        self._threshold_high_since: float | None = None
        self._low_yield_until = 0.0
        self._results: "queue.Queue[dict]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=timeout)

    def update_config(self, config: Config) -> None:
        self._config = config

    def drain_results(self) -> List[dict]:
        """取出所有待处理的自动清理结果（从主线程调用）。"""
        results = []
        try:
            while True:
                results.append(self._results.get_nowait())
        except queue.Empty:
            pass
        return results

    def _run(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(5)

    def _tick(self) -> None:
        cfg = self._config
        now = time.monotonic()

        triggered_by = None

        if cfg.threshold_enabled:
            stats = self._monitor.latest()
            threshold = int(cfg.threshold_percent)
            trigger_seconds = max(
                0,
                min(300, int(getattr(cfg, "threshold_trigger_seconds", 5))),
            )
            if (
                threshold != self._last_threshold_percent
                or trigger_seconds != self._last_threshold_trigger_seconds
            ):
                self._threshold_high_since = None
                self._low_yield_until = 0.0
                self._last_threshold_percent = threshold
                self._last_threshold_trigger_seconds = trigger_seconds
            cooldown_sec = max(
                1,
                int(getattr(
                    cfg,
                    "threshold_cooldown_seconds",
                    self.DEFAULT_THRESHOLD_COOLDOWN_SECONDS,
                )),
            )
            pressure_high = bool(stats and memory_pressure_high(stats, threshold))
            if pressure_high:
                if self._threshold_high_since is None:
                    self._threshold_high_since = now
            else:
                self._threshold_high_since = None
            threshold_held = (
                pressure_high
                and self._threshold_high_since is not None
                and now - self._threshold_high_since >= trigger_seconds
            )
            if (
                stats
                and now >= self._low_yield_until
                and threshold_held
                and now - self._last_threshold_clean >= cooldown_sec
            ):
                triggered_by = "threshold"
        else:
            self._threshold_high_since = None
            self._low_yield_until = 0.0

        if not triggered_by and cfg.interval_enabled:
            interval_sec = max(1, cfg.interval_minutes) * 60
            if now - self._last_clean >= interval_sec:
                triggered_by = "interval"

        if not triggered_by:
            return

        if not self._trim_lock.acquire(blocking=False):
            return
        try:
            result = _core.trim_all_filtered(
                cfg.exclude_foreground_process,
                cfg.excluded_process_names,
                cfg.cleaning_mode,
                cfg.effective_clear_standby_too(),
            )
            result["trigger"] = triggered_by
        except OSError as e:
            result = {"error": str(e), "trigger": triggered_by}
        finally:
            self._trim_lock.release()

        self._last_clean = time.monotonic()
        if triggered_by == "threshold":
            self._last_threshold_clean = self._last_clean
            if result.get("freed_mb", 0.0) < self.LOW_YIELD_FREED_MB:
                cooldown_sec = max(
                    1,
                    int(getattr(
                        cfg,
                        "threshold_cooldown_seconds",
                        self.DEFAULT_THRESHOLD_COOLDOWN_SECONDS,
                    )),
                )
                self._low_yield_until = (
                    self._last_clean + cooldown_sec * self.LOW_YIELD_COOLDOWN_MULTIPLIER
                )
        self._results.put(result)

    @staticmethod
    def _available_floor(total_bytes: int) -> int:
        return available_floor(total_bytes)

    def _memory_pressure_high(self, stats: dict, threshold: int) -> bool:
        return memory_pressure_high(stats, threshold)
