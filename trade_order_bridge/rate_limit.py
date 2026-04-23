from __future__ import annotations

import threading
import time


class SlidingWindowRateLimiter:
    def __init__(self, limit_count: int, window_sec: int):
        self.limit_count = max(1, limit_count)
        self.window_sec = max(1, window_sec)
        self._lock = threading.Lock()
        self._events: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_sec
        with self._lock:
            existing = self._events.get(key, [])
            fresh = [value for value in existing if value >= cutoff]
            if len(fresh) >= self.limit_count:
                self._events[key] = fresh
                return False
            fresh.append(now)
            self._events[key] = fresh
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
