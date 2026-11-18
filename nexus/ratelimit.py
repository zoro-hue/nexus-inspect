"""Sliding-window rate limiter kept in process memory. Each Store owns one, so tests and separate servers do not
share counters. Limits reset when the process restarts; this is documented in the README."""
import threading
import time


class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__(f"Too many requests. Try again in {retry_after} seconds.")
        self.retry_after = retry_after


class Limiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits = {}
        self._lock = threading.Lock()

    def check(self, key, limit, window):
        """Record one hit for key. Raises RateLimited when the key already has `limit` hits in `window` seconds."""
        now = self._clock()
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if now - t < window]
            if len(recent) >= limit:
                self._hits[key] = recent
                raise RateLimited(max(1, int(window - (now - recent[0])) + 1))
            recent.append(now)
            self._hits[key] = recent
