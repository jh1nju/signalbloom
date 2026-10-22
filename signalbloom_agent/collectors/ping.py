"""Measure latency and packet loss with the system ping command (no admin rights)."""

import re
import subprocess
import sys

from . import CollectorError

COUNT = 4
RUN_TIMEOUT_SECONDS = 20

# A reply line always carries a TTL ("TTL=58" on Windows, "ttl=58" elsewhere).
# Lines like "Request timed out" or "Destination host unreachable" don't.
_TTL = re.compile(r"\bttl[=:]", re.IGNORECASE)
# The round-trip time: "time=8ms", "time<1ms", "time=4.12 ms". Only the "=" or "<"
# is matched, not the word, so localised Windows ("Zeit=8ms") works too.
_TIME = re.compile(r"[=<]\s*(\d+(?:[.,]\d+)?)\s*ms\b", re.IGNORECASE)


def ping_command(target: str, count: int = COUNT, wait_ms: int = 2000) -> list[str]:
    """Build the ping command. wait_ms is how long to wait for each reply."""
    if sys.platform == "win32":
        return ["ping", "-n", str(count), "-w", str(wait_ms), target]  # -w in ms
    if sys.platform == "darwin":
        return ["ping", "-c", str(count), "-W", str(wait_ms), target]  # -W in ms
    seconds = max(1, -(-wait_ms // 1000))  # Linux: -W in whole seconds, rounded up
    return ["ping", "-c", str(count), "-W", str(seconds), target]


def parse_ping_output(text: str, sent: int) -> dict:
    """Turn ping's output into latency_ms (average) and packet_loss_pct."""
    times = []
    for line in text.splitlines():
        if not _TTL.search(line):
            continue
        match = _TIME.search(line)
        if match:
            times.append(float(match.group(1).replace(",", ".")))
    times = times[:sent]  # Linux can print "(DUP!)" replies
    replies = len(times)
    return {
        "latency_ms": round(sum(times) / replies, 1) if replies else None,
        "packet_loss_pct": round(100 * (sent - replies) / sent, 1),
    }


def ping(target: str, count: int = COUNT) -> dict:
    """Ping `target` `count` times. Returns {"latency_ms", "packet_loss_pct"}."""
    try:
        result = subprocess.run(
            ping_command(target, count),
            capture_output=True,
            text=True,
            errors="replace",  # Windows may print in a non-UTF-8 code page
            timeout=RUN_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise CollectorError("The ping command was not found on this computer.") from None
    except subprocess.TimeoutExpired:
        return {"latency_ms": None, "packet_loss_pct": 100.0}
    # A non-zero exit code only means some replies were lost, so parse anyway.
    return parse_ping_output(result.stdout, count)
