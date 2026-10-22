import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from signalbloom_agent.collectors import CollectorError
from signalbloom_agent.collectors.ping import parse_ping_output, ping, ping_command

FIXTURES = Path(__file__).parent / "fixtures"
RUN = "signalbloom_agent.collectors.ping.subprocess.run"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "name, latency, loss",
    [
        # Windows' own summary says "Average = 6ms" (rounded); real average is 6.0.
        ("ping_windows_ok.txt", 6.0, 0.0),
        ("ping_windows_all_fail.txt", None, 100.0),
        # Windows counts "Destination host unreachable" as received (25% loss);
        # it isn't a reply from the target, so it counts as lost here.
        ("ping_windows_partial.txt", 13.5, 50.0),
        ("ping_windows_lt1ms.txt", 1.0, 0.0),
        ("ping_windows_de.txt", 10.0, 25.0),
        ("ping_linux_ok.txt", 4.5, 0.0),
        ("ping_linux_all_fail.txt", None, 100.0),
        ("ping_macos_ok.txt", 6.4, 0.0),
        ("ping_macos_all_fail.txt", None, 100.0),
    ],
)
def test_parser_against_fixtures(name, latency, loss):
    assert parse_ping_output(fixture(name), sent=4) == {
        "latency_ms": latency,
        "packet_loss_pct": loss,
    }


def test_duplicate_replies_are_capped():
    line = "64 bytes from 1.1.1.1: icmp_seq=1 ttl=58 time=4.0 ms\n"
    assert parse_ping_output(line * 5, sent=4)["packet_loss_pct"] == 0.0


@pytest.mark.parametrize(
    "platform, expected",
    [
        ("win32", ["ping", "-n", "4", "-w", "2000", "1.1.1.1"]),
        ("darwin", ["ping", "-c", "4", "-W", "2000", "1.1.1.1"]),
        ("linux", ["ping", "-c", "4", "-W", "2", "1.1.1.1"]),
    ],
)
def test_command_per_os(monkeypatch, platform, expected):
    monkeypatch.setattr("sys.platform", platform)
    assert ping_command("1.1.1.1") == expected


def test_ping_parses_output_even_on_nonzero_exit():
    result = MagicMock(stdout=fixture("ping_windows_all_fail.txt"), returncode=1)
    with patch(RUN, return_value=result) as run:
        assert ping("192.0.2.1") == {"latency_ms": None, "packet_loss_pct": 100.0}
    assert run.call_args.kwargs["timeout"] > 0
    assert "shell" not in run.call_args.kwargs


def test_ping_timeout_is_full_loss():
    with patch(RUN, side_effect=subprocess.TimeoutExpired("ping", 20)):
        assert ping("1.1.1.1") == {"latency_ms": None, "packet_loss_pct": 100.0}


def test_missing_ping_command_raises():
    with patch(RUN, side_effect=FileNotFoundError()):
        with pytest.raises(CollectorError, match="ping command was not found"):
            ping("1.1.1.1")
