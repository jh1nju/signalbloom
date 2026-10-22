import ipaddress
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from signalbloom_agent.collectors import devices
from signalbloom_agent.collectors.devices import (
    Check,
    DeviceScanner,
    Sighting,
    full_scan,
    normalise_mac,
    parse_arp_output,
    parse_proc_net_arp,
)

FIXTURES = Path(__file__).parent / "fixtures"
LAN = ipaddress.ip_network("192.168.1.0/24")
EXPECTED_LAN = {
    "192.168.1.1": "a4:2b:b0:aa:bb:cc",
    "192.168.1.20": "aa:bb:cc:dd:ee:01",
    "192.168.1.35": "3c:22:fb:0d:0e:0f",
}


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["arp_windows.txt", "arp_macos.txt", "arp_linux.txt"])
def test_arp_parser_against_fixtures(name):
    # Broadcast, multicast, incomplete and other-subnet entries are dropped.
    assert parse_arp_output(fixture(name), LAN) == EXPECTED_LAN


def test_arp_parser_without_subnet_keeps_other_interfaces():
    table = parse_arp_output(fixture("arp_windows.txt"))
    assert table["192.168.56.101"] == "08:00:27:11:22:33"
    assert "192.168.56.255" not in table  # broadcast MAC
    assert "224.0.0.22" not in table      # multicast MAC


def test_proc_net_arp_fallback():
    assert parse_proc_net_arp(fixture("proc_net_arp.txt"), LAN) == EXPECTED_LAN


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("AA-BB-CC-DD-EE-01", "aa:bb:cc:dd:ee:01"),
        ("3c:22:fb:d:e:f", "3c:22:fb:0d:0e:0f"),  # macOS drops leading zeros
        ("ff:ff:ff:ff:ff:ff", None),
        ("01-00-5e-00-00-16", None),
        ("00:00:00:00:00:00", None),
        ("aa:bb:cc", None),
    ],
)
def test_normalise_mac(raw, expected):
    assert normalise_mac(raw) == expected


def test_arp_missing_on_linux_reads_proc(monkeypatch):
    monkeypatch.setattr(devices.sys, "platform", "linux")
    with patch.object(devices.subprocess, "run", side_effect=FileNotFoundError), \
            patch.object(devices.Path, "read_text", return_value=fixture("proc_net_arp.txt")):
        assert devices.read_arp_table(LAN) == EXPECTED_LAN


def test_arp_timeout_gives_empty_table():
    with patch.object(devices.subprocess, "run",
                      side_effect=subprocess.TimeoutExpired("arp", 5)):
        assert devices.read_arp_table(LAN) == {}


# --- full_scan --------------------------------------------------------------

def test_full_scan_reports_ping_replies():
    pinged = []

    def fake_ping(ip):
        pinged.append(ip)
        return ip in ("192.168.1.1", "192.168.1.20")

    names = {"192.168.1.20": "reception-pc"}
    with patch.object(devices, "local_ipv4", return_value="192.168.1.7"), \
            patch.object(devices, "_ping_once", side_effect=fake_ping), \
            patch.object(devices, "read_arp_table", return_value=EXPECTED_LAN), \
            patch.object(devices, "_reverse_dns", side_effect=names.get):
        found = full_scan("1.1.1.1")

    assert len(pinged) == 253  # .1-.254 without our own .7
    assert "192.168.1.7" not in pinged
    assert found == [
        Sighting(device("192.168.1.1", "a4:2b:b0:aa:bb:cc"), True),
        Sighting(device("192.168.1.20", "aa:bb:cc:dd:ee:01", "reception-pc"), True),
        Sighting(device("192.168.1.35", "3c:22:fb:0d:0e:0f"), False),
    ]


def test_full_scan_respects_budget():
    def slow_dns(ip):
        time.sleep(2)
        return "too-late"

    started = time.monotonic()
    with patch.object(devices, "local_ipv4", return_value="192.168.1.7"), \
            patch.object(devices, "_ping_once", return_value=True), \
            patch.object(devices, "read_arp_table", return_value=EXPECTED_LAN), \
            patch.object(devices, "_reverse_dns", side_effect=slow_dns), \
            patch.object(devices, "DNS_RESERVE_SECONDS", 0.2):
        found = full_scan("1.1.1.1", budget=0.5)

    assert time.monotonic() - started < 1.5
    assert [s.device["hostname"] for s in found] == [None, None, None]


def test_full_scan_without_network():
    with patch.object(devices, "local_ipv4", return_value=None):
        assert full_scan("1.1.1.1") == []


def test_recheck_reports_ping_and_arp():
    known = [device("192.168.1.20", "aa:bb:cc:dd:ee:01"),
             device("192.168.1.35", "3c:22:fb:0d:0e:0f"),
             device("192.168.1.44", "aa:bb:cc:dd:ee:44")]
    with patch.object(devices, "_ping_once", side_effect=lambda ip: ip == "192.168.1.20"), \
            patch.object(devices, "read_arp_table", return_value=EXPECTED_LAN):
        assert devices.recheck(known) == {
            "aa:bb:cc:dd:ee:01": Check(replied=True, in_arp=True),
            "3c:22:fb:0d:0e:0f": Check(replied=False, in_arp=True),
            "aa:bb:cc:dd:ee:44": Check(replied=False, in_arp=False),
        }


# --- DeviceScanner ----------------------------------------------------------

PC = ("192.168.1.20", "aa:bb:cc:dd:ee:01")
FIREWALLED = ("192.168.1.35", "3c:22:fb:0d:0e:0f")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def device(ip, mac, hostname=None, online=True):
    return {"mac": mac, "ip": ip, "hostname": hostname, "vendor": None, "online": online}


def make_scanner(scans, rechecks=()):
    """A scanner fed scripted full-scan and recheck results, plus its clock."""
    clock = FakeClock()
    scans, rechecks = iter(scans), iter(rechecks)
    scanner = DeviceScanner("1.1.1.1", 300, clock=clock,
                            scan_fn=lambda probe: next(scans),
                            recheck_fn=lambda known: next(rechecks))
    return scanner, clock


def test_scanner_full_scan_then_recheck_then_full_scan():
    clock = FakeClock()
    scan = MagicMock(return_value=[Sighting(device(*PC, "pc"), True)])
    recheck = MagicMock(return_value={PC[1]: Check(True, True)})
    scanner = DeviceScanner("1.1.1.1", 300, clock=clock, scan_fn=scan, recheck_fn=recheck)

    assert scanner.devices() == [device(*PC, "pc")]
    assert scan.call_count == 1 and recheck.call_count == 0

    clock.now += 30  # between full scans: only the quick recheck
    scanner.devices()
    assert scan.call_count == 1 and recheck.call_count == 1

    clock.now += 300  # interval passed: full scan again
    scanner.devices()
    assert scan.call_count == 2


def test_ping_answering_device_needs_two_misses_in_a_row():
    miss = {PC[1]: Check(replied=False, in_arp=True)}  # a stale ARP entry is ignored
    hit = {PC[1]: Check(replied=True, in_arp=True)}
    scanner, clock = make_scanner(
        scans=[[Sighting(device(*PC), True)]],
        rechecks=[miss, hit, miss, miss],
    )
    assert scanner.devices()[0]["online"] is True   # full scan: replied
    clock.now += 30
    assert scanner.devices()[0]["online"] is True   # 1 miss: still online
    clock.now += 30
    assert scanner.devices()[0]["online"] is True   # a reply resets the count
    clock.now += 30
    assert scanner.devices()[0]["online"] is True   # 1 miss again
    clock.now += 30
    assert scanner.devices()[0]["online"] is False  # 2 in a row: offline


def test_offline_device_comes_back_on_first_reply():
    miss = {PC[1]: Check(False, False)}
    scanner, clock = make_scanner(
        scans=[[Sighting(device(*PC), True)]],
        rechecks=[miss, miss, {PC[1]: Check(True, True)}],
    )
    scanner.devices()
    for _ in range(2):
        clock.now += 30
        scanner.devices()
    assert scanner._known[PC[1]]["online"] is False
    clock.now += 30
    assert scanner.devices()[0]["online"] is True


def test_never_pinged_device_falls_back_to_arp():
    scanner, clock = make_scanner(
        scans=[[Sighting(device(*FIREWALLED), False)]],
        rechecks=[{FIREWALLED[1]: Check(False, True)}, {FIREWALLED[1]: Check(False, False)}],
    )
    assert scanner.devices()[0]["online"] is True   # in ARP after the sweep
    clock.now += 30
    assert scanner.devices()[0]["online"] is True   # still in ARP
    clock.now += 30
    assert scanner.devices()[0]["online"] is False  # gone from ARP: offline at once


def test_device_that_starts_answering_ping_switches_to_ping_rule():
    scanner, clock = make_scanner(
        scans=[[Sighting(device(*FIREWALLED), False)]],
        rechecks=[{FIREWALLED[1]: Check(True, True)}, {FIREWALLED[1]: Check(False, False)}],
    )
    scanner.devices()
    clock.now += 30
    scanner.devices()  # its first reply
    clock.now += 30
    assert scanner.devices()[0]["online"] is True  # now 1 miss is not enough


def test_missing_from_full_scan_counts_as_a_miss():
    scanner, clock = make_scanner(scans=[
        [Sighting(device(*PC, "pc"), True), Sighting(device(*FIREWALLED), False)],
        [Sighting(device(*FIREWALLED), False)],
        [Sighting(device(*FIREWALLED), False)],
    ])
    scanner.devices()
    clock.now += 300
    # The PC stays in the list, and one miss keeps it online.
    assert scanner.devices() == [device(*PC, "pc"), device(*FIREWALLED)]
    clock.now += 300
    assert scanner.devices() == [device(*PC, "pc", online=False), device(*FIREWALLED)]


def test_never_pinged_device_missing_from_full_scan_is_offline():
    scanner, clock = make_scanner(scans=[[Sighting(device(*FIREWALLED), False)], []])
    scanner.devices()
    clock.now += 300
    assert scanner.devices() == [device(*FIREWALLED, online=False)]


def test_scanner_keeps_hostname_when_dns_misses():
    scanner, clock = make_scanner(scans=[
        [Sighting(device(*PC, "pc"), True)],
        [Sighting(device("192.168.1.21", PC[1], None), True)],  # new IP, DNS timed out
    ])
    scanner.devices()
    clock.now += 300
    assert scanner.devices() == [device("192.168.1.21", PC[1], "pc")]


def test_scanner_returns_copies():
    scanner, _ = make_scanner(scans=[[Sighting(device(*PC), True)]])
    scanner.devices()[0]["online"] = "changed"
    assert scanner._known[PC[1]]["online"] is True


@pytest.mark.parametrize(
    "replied, in_arp, expected",
    [
        (True, True, True),
        (False, True, True),    # firewalled PC: blocks ping but answers ARP
        (True, False, True),
        (False, False, False),
    ],
)
def test_arp_fallback_rule(replied, in_arp, expected):
    assert devices._is_online(replied, in_arp) is expected
