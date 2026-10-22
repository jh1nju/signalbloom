import logging
import shutil
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from signalbloom_agent.__main__ import main
from signalbloom_agent.agent import BandwidthWorker, DeviceWorker, run
from signalbloom_agent.buffer import ReadingBuffer
from signalbloom_agent.collectors.bandwidth import BandwidthTest
from signalbloom_agent.config import Config
from signalbloom_agent.sender import AuthError, SendSummary

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"
DEVICES = [{"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.20",
            "hostname": "reception-pc", "vendor": None, "online": True}]


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class FakeDevices:
    def __init__(self):
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def snapshot(self):
        return [dict(d) for d in DEVICES]

    def current(self):
        return self.snapshot(), 1


class RecordingSender:
    """Records what was in the buffer at each flush, then 'sends' it all."""

    def __init__(self, buffer):
        self.buffer = buffer
        self.flushes = []  # (clock time, readings, devices)
        self.clock = None

    def flush(self, devices, scan=None):
        readings = self.buffer.oldest(len(self.buffer))
        self.flushes.append((self.clock(), readings, devices))
        self.buffer.remove_oldest(len(readings))
        return SendSummary(accepted=len(readings), requests=1)


def make_config(tmp_path) -> Config:
    return Config(cloud_url="http://localhost:8080", setup_code=None, site_id="site_8f3a2c",
                  site_token="sbt_test_x", interval_seconds=30,
                  bandwidth_interval_seconds=900, data_dir=tmp_path)


def test_loop_sends_on_schedule_with_bandwidth_in_background(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="signalbloom_agent")
    clock = FakeClock()
    stop = threading.Event()
    buffer = ReadingBuffer(tmp_path, 100)
    sender = RecordingSender(buffer)
    sender.clock = clock

    release = threading.Event()

    def slow_measure(_size_mb):
        release.wait(5)
        return 87.3

    bandwidth = BandwidthWorker(BandwidthTest(25, 900, clock=clock, measure=slow_measure))
    calls = []

    def measure():
        calls.append(clock.t)
        clock.t += 4.0  # pinging takes 4 s
        if len(calls) == 2:
            # The speed test started after reading 1 is still running during
            # this ping; let it finish before the reading is built.
            release.set()
            bandwidth._thread.join(5)
        if len(calls) == 3:
            stop.set()
        return {"measured_at": f"r{len(calls)}", "download_mbps": "not set"}

    devices = FakeDevices()
    run(make_config(tmp_path), stop=stop, clock=clock, sleep=clock.sleep, measure=measure,
        buffer=buffer, sender=sender, devices=devices, bandwidth=bandwidth)

    assert calls == [0.0, 30.0, 60.0]  # no drift from the 4 s pings
    assert [f[0] for f in sender.flushes] == [4.0, 34.0, 64.0]
    speeds = [f[1][0]["download_mbps"] for f in sender.flushes]
    assert speeds == [None, 87.3, None]  # result lands in the next reading only
    assert all(f[2] == DEVICES for f in sender.flushes)
    assert caplog.text.count("latency may be raised by the speed test") == 1
    assert devices.started and devices.stopped


def test_ctrl_c_stops_the_device_worker(tmp_path):
    def sleep(_):
        raise KeyboardInterrupt

    buffer = ReadingBuffer(tmp_path, 100)
    sender = RecordingSender(buffer)
    sender.clock = lambda: 0
    devices = FakeDevices()
    bandwidth = MagicMock(running=False)
    bandwidth.take_result.return_value = None
    with pytest.raises(KeyboardInterrupt):
        run(make_config(tmp_path), sleep=sleep, measure=lambda: {"measured_at": "x"},
            buffer=buffer, sender=sender, devices=devices, bandwidth=bandwidth)
    assert devices.stopped
    assert len(sender.flushes) == 1


def test_full_buffer_is_logged(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="signalbloom_agent")
    stop = threading.Event()
    buffer = ReadingBuffer(tmp_path, 1)
    buffer.add({"measured_at": "old"})
    sender = MagicMock()
    sender.flush.side_effect = lambda _d, scan=None: (stop.set(), SendSummary(error="cloud unreachable",
                                                                   buffered=1))[1]
    bandwidth = MagicMock(running=False)
    bandwidth.take_result.return_value = None
    run(make_config(tmp_path), stop=stop, sleep=lambda _: None,
        measure=lambda: {"measured_at": "new"}, buffer=buffer, sender=sender,
        devices=FakeDevices(), bandwidth=bandwidth)
    assert "1 readings from an earlier run" in caplog.text
    assert "buffer full, dropped 1 oldest reading" in caplog.text
    assert "cloud unreachable, 1 reading buffered" in caplog.text
    assert buffer.oldest(1)[0]["measured_at"] == "new"


def test_device_worker_keeps_last_list_when_a_scan_fails():
    scanner = MagicMock()
    scanner.devices.side_effect = [DEVICES, RuntimeError("boom")]
    worker = DeviceWorker(scanner, 20)
    assert worker.snapshot() == []
    worker.run_once()
    worker.run_once()
    snap = worker.snapshot()
    assert snap == DEVICES
    snap[0]["online"] = False
    assert worker.snapshot() == DEVICES  # a copy


def test_device_worker_thread_starts_and_stops():
    scanner = MagicMock()
    scanned = threading.Event()
    scanner.devices.side_effect = lambda: (scanned.set(), DEVICES)[1]
    worker = DeviceWorker(scanner, 20)
    worker.start()
    assert scanned.wait(5)
    worker.stop()
    assert not worker._thread.is_alive()


@pytest.fixture
def registered_config_dir(tmp_path, monkeypatch):
    text = EXAMPLE.read_text(encoding="utf-8")
    text = text.replace("site_id:\n", 'site_id: "site_8f3a2c"\n')
    text = text.replace("site_token:\n", 'site_token: "sbt_test_x"\n')
    (tmp_path / "config.yaml").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    logger = logging.getLogger("signalbloom_agent")
    for handler in list(logger.handlers):  # release agent.log so tmp can be removed
        logger.removeHandler(handler)
        handler.close()


def test_main_ctrl_c_exits_cleanly(registered_config_dir, capsys):
    with patch("signalbloom_agent.__main__.run", side_effect=KeyboardInterrupt):
        assert main([]) == 0
    assert "Stopped." in capsys.readouterr().out
    assert (registered_config_dir / "data" / "agent.log").is_file()


def test_main_auth_error_exits_with_message(registered_config_dir, capsys):
    with patch("signalbloom_agent.__main__.run", side_effect=AuthError("Register again.")):
        assert main([]) == 1
    assert "Register again." in capsys.readouterr().err


def test_device_worker_counts_full_scans():
    class Scanner:
        last_full_scan = None
        full = [True, False, True]

        def devices(self):
            if self.full.pop(0):
                self.last_full_scan = (self.last_full_scan or 0) + 300
            return DEVICES

    worker = DeviceWorker(Scanner(), 20)
    ids = []
    for _ in range(3):
        worker.run_once()
        ids.append(worker.current()[1])
    assert ids == [1, 1, 2]
