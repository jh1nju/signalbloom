import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from signalbloom_agent.__main__ import main

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"
API_READING_KEYS = {
    "measured_at", "latency_ms", "packet_loss_pct", "internet_up",
    "download_mbps", "upload_mbps", "error_rate_pct",
}


@pytest.fixture
def in_config_dir(tmp_path, monkeypatch):
    shutil.copy(EXAMPLE, tmp_path / "config.yaml")
    monkeypatch.chdir(tmp_path)


def fake_ping(target):
    if target == "1.1.1.1":
        return {"latency_ms": 6.0, "packet_loss_pct": 0.0}
    return {"latency_ms": 1.2, "packet_loss_pct": 0.0}


DEVICES = [{"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.20",
            "hostname": "reception-pc", "vendor": None, "online": True}]


@pytest.fixture(autouse=True)
def no_network_heavy_collectors():
    """The bandwidth test and device scan are mocked in every test here."""
    with patch("signalbloom_agent.__main__.measure_download_mbps", return_value=87.3) as m,             patch("signalbloom_agent.__main__.DeviceScanner") as scanner:
        scanner.return_value.devices.return_value = DEVICES
        yield m, scanner


def test_collect_once_prints_api_reading(in_config_dir, capsys, no_network_heavy_collectors):
    measure, scanner = no_network_heavy_collectors
    with patch("signalbloom_agent.__main__.ping", side_effect=fake_ping), patch(
        "signalbloom_agent.__main__.internet_up", return_value=True
    ), patch(
        "signalbloom_agent.__main__.find_default_gateway", return_value="192.168.88.1"
    ), patch("signalbloom_agent.__main__.ensure_registered") as register:
        assert main(["--collect-once"]) == 0

    register.assert_not_called()
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert set(body) == {"readings", "devices"}
    reading = body["readings"][0]
    assert set(reading) == API_READING_KEYS
    assert reading["latency_ms"] == 6.0
    assert reading["packet_loss_pct"] == 0.0
    assert reading["internet_up"] is True
    assert reading["download_mbps"] == 87.3
    assert reading["upload_mbps"] is None
    measure.assert_called_once_with(25)
    scanner.assert_called_once_with("1.1.1.1", 300)
    assert body["devices"] == DEVICES
    assert "Router 192.168.88.1 (local only): 1.2 ms" in out.err


def test_collect_once_without_router(in_config_dir, capsys):
    with patch("signalbloom_agent.__main__.ping", side_effect=fake_ping), patch(
        "signalbloom_agent.__main__.internet_up", return_value=True
    ), patch("signalbloom_agent.__main__.find_default_gateway", return_value=None):
        assert main(["--collect-once"]) == 0
    assert "Router: not found" in capsys.readouterr().err
