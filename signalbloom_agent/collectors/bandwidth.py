"""Estimate download speed by timing an HTTPS download of a fixed-size file."""

import time

import requests

# Cloudflare returns exactly `bytes` bytes of data over HTTPS.
TEST_URL = "https://speed.cloudflare.com/__down?bytes={size}"
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10
MAX_SECONDS = 60          # stop a test on a slow link and use what arrived
CHUNK_BYTES = 64 * 1024
MIN_COMPLETE = 0.9        # a body shorter than this (without hitting MAX_SECONDS) is a failure


def measure_download_mbps(size_mb: int) -> float | None:
    """Download `size_mb` megabytes and return the speed in Mbps, or None if it failed."""
    expected = size_mb * 1_000_000
    first_chunk = 0
    timed_bytes = 0
    started = None
    hit_time_limit = False
    try:
        with requests.get(
            TEST_URL.format(size=expected),
            stream=True,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            # Ask for raw bytes: decompressed bytes would overstate the speed.
            headers={"Accept-Encoding": "identity"},
        ) as response:
            if not 200 <= response.status_code < 300:
                return None
            for chunk in response.iter_content(CHUNK_BYTES):
                now = time.monotonic()
                if started is None:
                    # The timer starts at the first byte, so DNS, TLS and the
                    # server's start-up time are not counted as slow download.
                    started = now
                    first_chunk = len(chunk)
                    continue
                timed_bytes += len(chunk)
                if now - started >= MAX_SECONDS:
                    hit_time_limit = True
                    break
            elapsed = time.monotonic() - started if started is not None else 0.0
    except requests.RequestException:
        return None

    if elapsed <= 0 or timed_bytes == 0:
        return None
    if not hit_time_limit and first_chunk + timed_bytes < expected * MIN_COMPLETE:
        return None
    return round(timed_bytes * 8 / elapsed / 1_000_000, 1)


class BandwidthTest:
    """Runs the download test at most every `interval_seconds`."""

    def __init__(self, size_mb: int, interval_seconds: int, *, clock=time.monotonic,
                 measure=measure_download_mbps):
        self._size_mb = size_mb
        self._interval = interval_seconds
        self._clock = clock
        self._measure = measure
        self._last_run: float | None = None

    def due(self) -> bool:
        return self._last_run is None or self._clock() - self._last_run >= self._interval

    def maybe_run(self) -> float | None:
        """Download speed if a test was due (and worked), otherwise None."""
        if not self.due():
            return None
        self._last_run = self._clock()
        return self._measure(self._size_mb)
