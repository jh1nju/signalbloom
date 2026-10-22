"""Find the default gateway (the router) automatically."""

import ipaddress
import re
import socket
import struct
import subprocess
import sys
from pathlib import Path

RUN_TIMEOUT_SECONDS = 5


def find_default_gateway() -> str | None:
    """Return the router's IPv4 address, or None if it can't be found."""
    try:
        if sys.platform == "win32":
            return _gateway_from_windows_route(_run(["route", "print", "-4", "0.0.0.0"]))
        if sys.platform == "darwin":
            return _gateway_from_macos_route(_run(["route", "-n", "get", "default"]))
        return _gateway_from_proc_net_route(Path("/proc/net/route").read_text())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _run(command: list[str]) -> str:
    return subprocess.run(
        command, capture_output=True, text=True, errors="replace",
        timeout=RUN_TIMEOUT_SECONDS,
    ).stdout


def _is_ipv4(text: str) -> bool:
    try:
        ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return True


def _gateway_from_windows_route(text: str) -> str | None:
    """Pick the gateway from `route print -4 0.0.0.0` output.

    Active default routes look like:
        0.0.0.0          0.0.0.0     192.168.88.1     192.168.88.7     35
        (destination)    (netmask)   (gateway)        (interface)      (metric)
    """
    best = None  # (metric, gateway)
    for line in text.splitlines():
        fields = line.split()
        if (len(fields) == 5 and fields[0] == fields[1] == "0.0.0.0"
                and _is_ipv4(fields[2]) and fields[4].isdigit()):
            metric = int(fields[4])
            if best is None or metric < best[0]:
                best = (metric, fields[2])
    return best[1] if best else None


def _gateway_from_macos_route(text: str) -> str | None:
    match = re.search(r"^\s*gateway:\s*(\S+)", text, re.MULTILINE)
    if match and _is_ipv4(match.group(1)):
        return match.group(1)
    return None


def _gateway_from_proc_net_route(text: str) -> str | None:
    """Linux: rows with Destination 00000000 and the gateway flag (0x2) set."""
    best = None
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 7 or fields[1] != "00000000":
            continue
        if not int(fields[3], 16) & 0x2:
            continue
        # The kernel stores the address as little-endian hex.
        gateway = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
        metric = int(fields[6])
        if best is None or metric < best[1]:
            best = (gateway, metric)
    return best[0] if best else None
