import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from signalbloom_agent.buffer import ReadingBuffer
from signalbloom_agent.config import Config
from signalbloom_agent.sender import MAX_BATCH, REJECTED_FILE, AuthError, Sender

TOKEN = "sbt_test_SECRET_do_not_print_456"
DEVICES = [{"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.20",
            "hostname": "reception-pc", "vendor": None, "online": True}]


def make_config(data_dir: Path) -> Config:
    return Config(cloud_url="http://localhost:8080", setup_code=None, site_id="site_8f3a2c",
                  site_token=TOKEN, interval_seconds=30, bandwidth_interval_seconds=900,
                  data_dir=data_dir)


def reading(i: int) -> dict:
    return {"measured_at": f"t{i:05d}", "latency_ms": float(i)}


def response(status: int, payload=None):
    r = MagicMock(status_code=status)
    r.json.return_value = payload if payload is not None else {}
    return r


def accepted(readings) -> MagicMock:
    return response(202, {"accepted": len(readings), "duplicates": 0})


class FakeCloud:
    """Stands in for requests.post. `rule(readings)` returns a response or raises."""

    def __init__(self, rule):
        self.rule = rule
        self.calls = []  # list of (readings, kwargs)

    def __call__(self, url, **kwargs):
        readings = kwargs["json"]["readings"]
        self.calls.append((readings, kwargs))
        return self.rule(readings)

    def sent(self) -> list[list[str]]:
        return [[r["measured_at"] for r in readings] for readings, _ in self.calls]


@pytest.fixture
def setup(tmp_path):
    def make(rule, n=1, cap=3000):
        buffer = ReadingBuffer(tmp_path, cap)
        for i in range(n):
            buffer.add(reading(i))
        cloud = FakeCloud(rule)
        sender = Sender(make_config(tmp_path), buffer, post=cloud, now=lambda: "NOW")
        return sender, buffer, cloud
    return make


def test_202_sends_body_and_empties_buffer(setup):
    sender, buffer, cloud = setup(accepted)
    summary = sender.flush(DEVICES)

    assert len(buffer) == 0
    (readings, kwargs), = cloud.calls
    assert kwargs["json"] == {"site_id": "site_8f3a2c", "sent_at": "NOW",
                              "readings": [reading(0)], "devices": DEVICES}
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert kwargs["timeout"] > 0
    assert summary.lines() == ["sent 1 reading, 0 duplicates"]


def test_duplicates_are_counted(setup):
    sender, buffer, _ = setup(lambda rs: response(202, {"accepted": 1, "duplicates": 2}), n=3)
    assert sender.flush([]).lines() == ["sent 3 readings, 2 duplicates"]
    assert len(buffer) == 0


@pytest.mark.parametrize("error", [requests.ConnectionError(), requests.Timeout()])
def test_unreachable_keeps_readings(setup, error):
    def rule(_):
        raise error
    sender, buffer, _ = setup(rule, n=3)
    summary = sender.flush([])
    assert len(buffer) == 3
    assert summary.lines() == ["cloud unreachable, 3 readings buffered"]


@pytest.mark.parametrize("status", [500, 503, 429])
def test_server_error_keeps_readings(setup, status):
    sender, buffer, _ = setup(lambda _: response(status), n=2)
    summary = sender.flush([])
    assert len(buffer) == 2
    assert summary.lines() == [f"cloud error (HTTP {status}), 2 readings buffered"]


def test_backlog_goes_oldest_first_in_batches_of_500(setup):
    sender, buffer, cloud = setup(accepted, n=750)
    summary = sender.flush([])

    sent = cloud.sent()
    assert [len(b) for b in sent] == [MAX_BATCH, 250]
    assert sent[0][0] == "t00000" and sent[1][-1] == "t00749"
    assert len(buffer) == 0
    assert summary.lines() == ["sent 750 readings in 2 requests, 0 duplicates"]


def test_failure_on_second_batch_keeps_only_unsent(setup):
    calls = iter([True, False])

    def rule(readings):
        if next(calls):
            return accepted(readings)
        raise requests.ConnectionError()

    sender, buffer, _ = setup(rule, n=600)
    summary = sender.flush([])
    assert len(buffer) == 100
    assert buffer.oldest(1)[0]["measured_at"] == "t00500"
    assert summary.lines() == ["sent 500 readings, 0 duplicates",
                               "cloud unreachable, 100 readings buffered"]


@pytest.mark.parametrize("status, words", [(401, "no longer accepts"),
                                           (403, "belongs to another site")])
def test_401_and_403_stop_with_reregister_message(setup, status, words):
    sender, buffer, _ = setup(lambda _: response(status), n=2)
    with pytest.raises(AuthError) as exc_info:
        sender.flush([])
    message = str(exc_info.value)
    assert words in message
    assert "setup_code" in message and "site_token" in message
    assert TOKEN not in message
    assert len(buffer) == 2  # kept for after re-registering


def test_413_splits_until_batches_fit(setup):
    def rule(readings):
        return response(413) if len(readings) > 2 else accepted(readings)

    sender, buffer, cloud = setup(rule, n=5)
    summary = sender.flush([])

    assert len(buffer) == 0
    accepted_batches = [b for b in cloud.sent() if len(b) <= 2]
    assert sum(accepted_batches, []) == [f"t{i:05d}" for i in range(5)]  # in order
    assert summary.accepted == 5
    assert summary.rejected == []


def test_400_isolates_and_saves_only_the_bad_reading(setup, tmp_path):
    def rule(readings):
        if any(r["measured_at"] == "t00002" for r in readings):
            return response(400, {"error": {"code": "invalid_body", "message": "bad latency"}})
        return accepted(readings)

    sender, buffer, _ = setup(rule, n=6)
    summary = sender.flush([])

    assert len(buffer) == 0
    assert summary.accepted == 5
    lines = (tmp_path / REJECTED_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["reading"] == reading(2)
    assert saved["status"] == 400 and saved["message"] == "bad latency"
    assert summary.lines()[0] == (
        f"rejected reading from t00002 (HTTP 400: bad latency), saved to {REJECTED_FILE}")


def test_single_reading_413_is_rejected_not_looped(setup, tmp_path):
    sender, buffer, cloud = setup(lambda _: response(413), n=1)
    sender.flush([])
    assert len(buffer) == 0
    assert len(cloud.calls) == 1
    assert (tmp_path / REJECTED_FILE).is_file()


def test_unreachable_during_split_keeps_the_rest(setup):
    def rule(readings):
        if len(readings) > 1:
            return response(413)
        if readings[0]["measured_at"] == "t00000":
            return accepted(readings)
        raise requests.ConnectionError()

    sender, buffer, _ = setup(rule, n=4)
    summary = sender.flush([])
    assert [r["measured_at"] for r in buffer.oldest(10)] == ["t00001", "t00002", "t00003"]
    assert summary.error == "cloud unreachable"


def test_token_never_logged_or_shown(setup, caplog):
    caplog.set_level(logging.DEBUG)
    sender, _, _ = setup(accepted)
    for line in sender.flush([]).lines():
        logging.getLogger("signalbloom_agent").info(line)
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(sender)


def devices_rule(readings, kwargs):
    """The cloud refuses any request that carries a device list."""
    if kwargs["json"]["devices"]:
        return response(400, {"error": {"code": "invalid_body", "message": "bad device"}})
    return accepted(readings)


class DeviceAwareCloud(FakeCloud):
    def __call__(self, url, **kwargs):
        self.calls.append((kwargs["json"]["readings"], kwargs))
        return self.rule(kwargs["json"]["readings"], kwargs)


def test_rejected_device_list_is_saved_and_paused_until_next_scan(tmp_path):
    buffer = ReadingBuffer(tmp_path, 100)
    for i in range(6):
        buffer.add(reading(i))
    cloud = DeviceAwareCloud(devices_rule)
    sender = Sender(make_config(tmp_path), buffer, post=cloud, now=lambda: "NOW")

    summary = sender.flush(DEVICES, scan=1)

    # Every reading got through; none were rejected.
    assert len(buffer) == 0
    assert summary.accepted == 6 and summary.rejected == []
    saved = [json.loads(line) for line in
             (tmp_path / REJECTED_FILE).read_text(encoding="utf-8").splitlines()]
    assert len(saved) == 1
    assert saved[0]["devices"] == DEVICES and "reading" not in saved[0]
    assert saved[0]["status"] == 400 and saved[0]["message"] == "bad device"
    assert summary.lines()[0] == (
        f"cloud rejected the device list (HTTP 400: bad device), saved to {REJECTED_FILE}; "
        "sending readings without devices until the next full scan")
    # After the pause starts, the rest of the flush sends no devices.
    first_ok = next(i for i, (_, kw) in enumerate(cloud.calls) if not kw["json"]["devices"])
    assert all(kw["json"]["devices"] == [] for _, kw in cloud.calls[first_ok:])

    # Same scan: still no devices, and no new device rejection.
    buffer.add(reading(10))
    summary = sender.flush(DEVICES, scan=1)
    assert cloud.calls[-1][1]["json"]["devices"] == []
    assert summary.lines() == ["sent 1 reading, 0 duplicates"]

    # A new full scan: devices are tried again.
    cloud.rule = lambda readings, kwargs: accepted(readings)
    buffer.add(reading(11))
    summary = sender.flush(DEVICES, scan=2)
    assert cloud.calls[-1][1]["json"]["devices"] == DEVICES
    assert summary.lines()[0] == "new device scan, sending the device list again"


def test_bad_reading_with_devices_is_still_rejected_and_devices_kept(tmp_path):
    def rule(readings, kwargs):
        if any(r["measured_at"] == "t00001" for r in readings):
            return response(400)
        return accepted(readings)

    buffer = ReadingBuffer(tmp_path, 100)
    for i in range(3):
        buffer.add(reading(i))
    cloud = DeviceAwareCloud(rule)
    sender = Sender(make_config(tmp_path), buffer, post=cloud, now=lambda: "NOW")

    summary = sender.flush(DEVICES, scan=1)
    assert [m for m, _, _ in summary.rejected] == ["t00001"]
    assert summary.devices_rejected is None
    saved = json.loads((tmp_path / REJECTED_FILE).read_text(encoding="utf-8"))
    assert saved["reading"] == reading(1)

    buffer.add(reading(5))
    sender.flush(DEVICES, scan=1)
    assert cloud.calls[-1][1]["json"]["devices"] == DEVICES  # not paused


def test_retry_without_devices_hitting_503_stops_and_keeps_reading(tmp_path):
    def rule(readings, kwargs):
        return response(400) if kwargs["json"]["devices"] else response(503)

    buffer = ReadingBuffer(tmp_path, 100)
    buffer.add(reading(0))
    sender = Sender(make_config(tmp_path), buffer, post=DeviceAwareCloud(rule), now=lambda: "NOW")
    summary = sender.flush(DEVICES, scan=1)
    assert len(buffer) == 1
    assert summary.error == "cloud error (HTTP 503)"
    assert not (tmp_path / REJECTED_FILE).exists()
