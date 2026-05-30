"""后台内存采样器。

在守护线程中运行，每秒向队列推入新的快照。
UI 会在 Tk 主循环中消费队列。
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Deque, Optional

from . import _core


class Monitor:
    HISTORY = 60  # 内存百分比历史秒数

    def __init__(self, interval: float = 1.0) -> None:
        self._interval = interval
        self._q: "queue.Queue[dict]" = queue.Queue(maxsize=self.HISTORY)
        self._stop = threading.Event()
        self._history: Deque[float] = deque(maxlen=self.HISTORY)
        self._latest: Optional[dict] = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                stats = _core.memory_stats()
            except OSError:
                stats = None
            if stats is not None:
                with self._lock:
                    self._latest = stats
                    self._history.append(float(stats["percent"]))
                # 队列满时丢弃最旧数据，再写入新快照。
                # 采用无锁处理：只清掉过期项。
                while self._q.full():
                    try:
                        self._q.get_nowait()
                    except queue.Empty:
                        break
                try:
                    self._q.put_nowait(stats)
                except queue.Full:
                    pass  # 其他线程已消费或填充，安全跳过
            self._stop.wait(self._interval)

    def drain_latest(self) -> Optional[dict]:
        if self._q.empty():
            return None
        latest: Optional[dict] = None
        try:
            while True:
                latest = self._q.get_nowait()
        except queue.Empty:
            pass
        return latest

    def history(self) -> list[float]:
        with self._lock:
            return list(self._history)

    def latest(self) -> Optional[dict]:
        with self._lock:
            return dict(self._latest) if self._latest else None
