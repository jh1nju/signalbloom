"""Find devices on the local network without admin rights.

A full scan pings every address in the local /24, then reads the system ARP
table for MAC addresses and tries reverse DNS for names. It is heavy, so
DeviceScanner runs it only every device_scan_seconds. In between it rechecks
just the devices it already knows, which takes about a second.

Online status: a device that has ever answered a ping is judged by ping alone,
and goes offline after MISSES_BEFORE_OFFLINE failed checks in a row. A device
that never answers ping (e.g. a firewalled PC) falls back to the ARP table.
"""

import ipaddress
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .ping import parse_ping_output, ping_command

SCAN_BUDGET_SECONDS = 30      # a full scan must finish within this
DNS_RESERVE_SECONDS = 8       # part of the budget kept back for reverse DNS
RECHECK_BUDGET_SECONDS = 5
PING_WAIT_MS = 500            # per-address reply timeout during a sweep
PING_RUN_TIMEOUT_SECONDS = 3
ARP_RUN_TIMEOUT_SECONDS = 5
WORKERS = 64
MISSES_BEFORE_OFFLINE = 2     # one dropped ping shouldn't raise a false alert

# Windows: "  192.168.1.20          aa-bb-cc-dd-ee-01     dynamic"
# The type word is not matched because Windows translates it.
_WINDOWS_ROW = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-f]{2}(?:-[0-9a-f]{2}){5})\b", re.IGNORECASE
)
# macOS / Linux: "? (192.168.1.20) at aa:bb:cc:dd:ee:1 on en0 ..."
# macOS drops leading zeros in each byte ("0:1a:..."), so 1 or 2 hex digits.
_UNIX_ROW = re.compile(
    r"\((\d{1,3}(?:\.\d{1,3}){3})\)\s+at\s+([0-9a-f]{1,2}(?::[0-9a-f]{1,2}){5})\b",
    re.IGNORECASE,
)


def normalise_mac(text: str) -> str | None:
    """'AA-BB-CC-D-E-1' -> 'aa:bb:cc:0d:0e:01'. None for broadcast, multicast or 00:00..."""
    parts = re.split(r"[-:]", text.strip())
    if len(parts) != 6:
        return None
    try:
        octets = [int(p, 16) for p in parts]
    except ValueError:
        return None
    if any(o > 0xFF for o in octets) or not any(octets):
        return None
    if octets[0] & 0x01:  # multicast bit (also covers ff:ff:ff:ff:ff:ff broadcast)
        return None
    return ":".join(f"{o:02x}" for o in octets)


def _in_network(ip: str, network: ipaddress.IPv4Network | None) -> bool:
    if network is None:
        return True
    address = ipaddress.IPv4Address(ip)
    return address in network and address not in (network.network_address,
                                                    network.broadcast_address)


def parse_arp_output(text: str, network: ipaddress.IPv4Network | None = None) -> dict[str, str]:
    """Turn `arp -a` (Windows) or `arp -an` (macOS/Linux) output into {ip: mac}.

    Incomplete entries, broadcast/multicast MACs and addresses outside
    `network` (when given) are skipped.
    """
    table = {}
    for line in text.splitlines():
        match = _WINDOWS_ROW.match(line) or _UNIX_ROW.search(line)
        if not match:
            continue  # headers, "(incomplete)", "<incomplete>"
        ip, mac = match.group(1), normalise_mac(match.group(2))
        try:
            if mac and _in_network(ip, network):
                table[ip] = mac
        except ipaddress.AddressValueError:
            continue
    return table


def parse_proc_net_arp(text: str, network: ipaddress.IPv4Network | None = None) -> dict[str, str]:
    """Linux fallback when the arp command isn't installed: read /proc/net/arp."""
    table = {}
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4 or fields[2] == "0x0":  # flags 0x0 = incomplete
            continue
        mac = normalise_mac(fields[3])
        try:
            if mac and _in_network(fields[0], network):
                table[fields[0]] = mac
        except ipaddress.AddressValueError:
            continue
    return table


def local_ipv4(probe: str) -> str | None:
    """This computer's IPv4 address on the interface that routes to `probe`.

    Connecting a UDP socket sends nothing; it just makes the OS pick a route.
    That skips virtual adapters (VirtualBox, VPN) that aren't the real LAN.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((probe, 80))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    if ip == "0.0.0.0" or ipaddress.IPv4Address(ip).is_loopback:
        return None
    return ip


def read_arp_table(network: ipaddress.IPv4Network | None = None) -> dict[str, str]:
    # -n (numeric) stops macOS/Linux doing a slow DNS lookup for every entry.
    command = ["arp", "-a"] if sys.platform == "win32" else ["arp", "-an"]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, errors="replace",
            timeout=ARP_RUN_TIMEOUT_SECONDS,
        )
        return parse_arp_output(result.stdout, network)
    except FileNotFoundError:
        if sys.platform.startswith("linux"):
            try:
                return parse_proc_net_arp(Path("/proc/net/arp").read_text(), network)
            except OSError:
                pass
        return {}
    except subprocess.SubprocessError:
        return {}


def _ping_once(ip: str) -> bool:
    """True if `ip` answers one ping within PING_WAIT_MS."""
    try:
        result = subprocess.run(
            ping_command(ip, count=1, wait_ms=PING_WAIT_MS),
            capture_output=True, text=True, errors="replace",
            timeout=PING_RUN_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return parse_ping_output(result.stdout, sent=1)["latency_ms"] is not None


def _reverse_dns(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return None


def _run_parallel(pool, fn, items, deadline: float) -> dict:
    """Run fn(item) for every item; return {item: result} for those done by `deadline`."""
    futures = {pool.submit(fn, item): item for item in items}
    done, _ = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
    results = {}
    for future in done:
        if future.exception() is None:
            results[futures[future]] = future.result()
    return results


def _is_online(replied_to_ping: bool, in_arp_table: bool) -> bool:
    """Online rule for devices that have never answered a ping.

    A device must answer ARP to use the LAN at all, so an ARP entry catches
    firewalled PCs. But entries outlive the device: Windows keeps listing stale
    entries and macOS keeps them about 20 minutes, so a device that has left
    can still count as online here for several minutes (docs/LIMITATIONS.md).
    """
    return replied_to_ping or in_arp_table


class Sighting(NamedTuple):
    """One device found by a full scan (it is always in the ARP table)."""
    device: dict
    replied: bool


class Check(NamedTuple):
    """What a recheck saw for one known device."""
    replied: bool
    in_arp: bool


@dataclass
class _History:
    answers_ping: bool = False  # has this device ever replied to a ping?
    misses: int = 0             # failed checks in a row since its last reply


def _sort_key(device: dict):
    return ipaddress.IPv4Address(device["ip"])


def full_scan(probe: str, budget: float = SCAN_BUDGET_SECONDS) -> list[Sighting]:
    """Ping the whole local /24, then read ARP and reverse DNS. Finishes within `budget`."""
    deadline = time.monotonic() + budget
    own_ip = local_ipv4(probe)
    if own_ip is None:
        return []
    network = ipaddress.ip_network(f"{own_ip}/24", strict=False)
    hosts = [str(h) for h in network.hosts() if str(h) != own_ip]

    pool = ThreadPoolExecutor(max_workers=WORKERS)
    try:
        replies = _run_parallel(pool, _ping_once, hosts, deadline - DNS_RESERVE_SECONDS)
        arp = read_arp_table(network)
        names = _run_parallel(pool, _reverse_dns, list(arp), deadline)
    finally:
        # Don't wait for stragglers: anything unfinished is simply left out.
        pool.shutdown(wait=False, cancel_futures=True)

    sightings = [
        Sighting(
            # "online" is a placeholder: DeviceScanner decides it from history.
            {"mac": mac, "ip": ip, "hostname": names.get(ip), "vendor": None, "online": True},
            replies.get(ip, False),
        )
        for ip, mac in arp.items()
    ]
    return sorted(sightings, key=lambda s: _sort_key(s.device))


def recheck(known: list[dict], budget: float = RECHECK_BUDGET_SECONDS) -> dict[str, Check]:
    """Ping only the devices we already know. Returns {mac: Check}."""
    if not known:
        return {}
    deadline = time.monotonic() + budget
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    try:
        replies = _run_parallel(pool, _ping_once, [d["ip"] for d in known], deadline)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    arp = read_arp_table()
    return {
        d["mac"]: Check(replies.get(d["ip"], False), arp.get(d["ip"]) == d["mac"])
        for d in known
    }


class DeviceScanner:
    """Keeps the device list: a full scan every `scan_seconds`, a quick recheck between."""

    def __init__(self, probe: str, scan_seconds: int, *, clock=time.monotonic,
                 scan_fn=full_scan, recheck_fn=recheck):
        self._probe = probe
        self._scan_seconds = scan_seconds
        self._clock = clock
        self._scan_fn = scan_fn
        self._recheck_fn = recheck_fn
        self._known: dict[str, dict] = {}  # mac -> device
        self._history: dict[str, _History] = {}  # mac -> ping history
        self._last_scan: float | None = None

    def devices(self) -> list[dict]:
        now = self._clock()
        if self._last_scan is None or now - self._last_scan >= self._scan_seconds:
            self._last_scan = now
            self._merge_scan(self._scan_fn(self._probe))
        else:
            for mac, check in self._recheck_fn(list(self._known.values())).items():
                if mac in self._known:
                    self._update(mac, check.replied, check.in_arp)
        return sorted((dict(d) for d in self._known.values()), key=_sort_key)

    def _merge_scan(self, found: list[Sighting]) -> None:
        seen = set()
        for sighting in found:
            device = dict(sighting.device)
            old = self._known.get(device["mac"])
            if old:
                device["online"] = old["online"]
                if device["hostname"] is None:
                    device["hostname"] = old["hostname"]
            self._known[device["mac"]] = device
            self._update(device["mac"], sighting.replied, in_arp=True)
            seen.add(device["mac"])
        # A device missing from this scan stays in the list and counts as a
        # failed check, so the cloud sees it go offline rather than vanish.
        for mac in self._known.keys() - seen:
            self._update(mac, replied=False, in_arp=False)

    def _update(self, mac: str, replied: bool, in_arp: bool) -> None:
        device = self._known[mac]
        history = self._history.setdefault(mac, _History())
        if replied:
            history.answers_ping = True
            history.misses = 0
            device["online"] = True
        elif history.answers_ping:
            # Judged by ping alone; stays as it was until enough misses in a row.
            history.misses += 1
            if history.misses >= MISSES_BEFORE_OFFLINE:
                device["online"] = False
        else:
            device["online"] = _is_online(replied, in_arp)
