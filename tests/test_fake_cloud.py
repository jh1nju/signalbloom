import sys
import threading
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import fake_cloud  # noqa: E402

from signalbloom_agent.buffer import ReadingBuffer  # noqa: E402
from signalbloom_agent.config import Config  # noqa: E402
from signalbloom_agent.commands import CommandPoller  # noqa: E402
from signalbloom_agent.sender import Sender  # noqa: E402

DEVICES = [{"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.20",
            "hostname": "reception-pc", "vendor": None, "online": True}]


@pytest.fixture
def start_cloud():
    servers = []

    def start(state_path=None):
        server = fake_cloud.make_server(0, state_path=state_path)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def register(url: str) -> dict:
    r = requests.post(url + "/api/agent/register", timeout=5, json={
        "setup_code": fake_cloud.SETUP_CODE, "agent_version": "test", "hostname": "h"})
    assert r.status_code == 201
    return r.json()


def test_sender_against_fake_cloud_and_summary(start_cloud, tmp_path):
    url = start_cloud()
    site = register(url)
    config = Config(cloud_url=url, setup_code=None, site_id=site["site_id"],
                    site_token=site["site_token"], interval_seconds=30,
                    bandwidth_interval_seconds=900, data_dir=tmp_path)
    buffer = ReadingBuffer(tmp_path, 100)
    for i in range(3):
        buffer.add({"measured_at": f"2026-11-03T14:30:0{i}+13:00", "latency_ms": 5.0})
    buffer.add({"measured_at": "2026-11-03T14:30:00+13:00", "latency_ms": 5.0})  # repeat

    summary = Sender(config, buffer).flush(DEVICES)
    assert summary.lines() == ["sent 4 readings, 1 duplicate"]
    assert len(buffer) == 0

    body = requests.get(url + "/debug/summary", timeout=5).json()
    assert body["total_readings"] == 3
    assert body["sites"][site["site_id"]] == {
        "readings": 3,
        "earliest_measured_at": "2026-11-03T14:30:00+13:00",
        "latest_measured_at": "2026-11-03T14:30:02+13:00",
        "devices": [{"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.20",
                     "hostname": "reception-pc", "online": True}],
        "commands": [],
    }


def test_state_file_keeps_token_across_restart(start_cloud, tmp_path):
    state = tmp_path / "state.json"
    site = register(start_cloud(state))

    url = start_cloud(state)  # a "restarted" cloud
    r = requests.post(url + "/api/ingest", timeout=5,
                      headers={"Authorization": f"Bearer {site['site_token']}"},
                      json={"site_id": site["site_id"], "sent_at": "x", "readings": [],
                            "devices": []})
    assert r.status_code == 202
    again = requests.post(url + "/api/agent/register", timeout=5, json={
        "setup_code": fake_cloud.SETUP_CODE, "agent_version": "t", "hostname": "h"})
    assert again.status_code == 410  # the code stays used


def test_without_state_path_nothing_is_written(start_cloud, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    register(start_cloud())
    assert list(tmp_path.iterdir()) == []


def ingest(url, site, measured_ats):
    r = requests.post(url + "/api/ingest", timeout=5,
                      headers={"Authorization": f"Bearer {site['site_token']}"},
                      json={"site_id": site["site_id"], "sent_at": "x", "devices": [],
                            "readings": [{"measured_at": t} for t in measured_ats]})
    assert r.status_code == 202
    return r.json()


def test_readings_survive_restart(start_cloud, tmp_path):
    state = tmp_path / "state.json"
    url = start_cloud(state)
    site = register(url)
    ingest(url, site, ["2026-11-03T14:30:00+13:00", "2026-11-03T14:30:30+13:00"])

    url = start_cloud(state)  # a "restarted" cloud
    summary = requests.get(url + "/debug/summary", timeout=5).json()
    assert summary["sites"][site["site_id"]]["readings"] == 2
    # The restarted cloud still knows them, so a resend counts as duplicates.
    assert ingest(url, site, ["2026-11-03T14:30:00+13:00"]) == {"accepted": 0, "duplicates": 1}


def test_earliest_and_latest_compare_times_not_text(start_cloud):
    url = start_cloud()
    site = register(url)
    # 14:00+13:00 is 01:00 UTC; 03:00+00:00 is later even though it sorts first as text.
    ingest(url, site, ["2026-11-03T14:00:00+13:00", "2026-11-03T03:00:00+00:00",
                       "not a time"])
    entry = requests.get(url + "/debug/summary", timeout=5).json()["sites"][site["site_id"]]
    assert entry["readings"] == 3
    assert entry["earliest_measured_at"] == "2026-11-03T14:00:00+13:00"
    assert entry["latest_measured_at"] == "2026-11-03T03:00:00+00:00"


def test_site_without_readings_has_no_range(start_cloud):
    url = start_cloud()
    site = register(url)
    entry = requests.get(url + "/debug/summary", timeout=5).json()["sites"][site["site_id"]]
    assert entry["earliest_measured_at"] is None and entry["latest_measured_at"] is None


def site_config(url, site, data_dir) -> Config:
    return Config(cloud_url=url, setup_code=None, site_id=site["site_id"],
                  site_token=site["site_token"], interval_seconds=30,
                  bandwidth_interval_seconds=900, data_dir=data_dir)


def queue(url, **body):
    return requests.post(url + "/debug/commands", timeout=5, json=body)


def test_queued_commands_run_and_results_show_in_summary(start_cloud, tmp_path):
    url = start_cloud()
    site = register(url)
    assert queue(url, type="restart_device", target_mac="aa:bb:cc:dd:ee:01").status_code == 201
    assert queue(url, type="shell", target_mac="aa:bb:cc:dd:ee:01").status_code == 201

    sleeps = []
    poller = CommandPoller(site_config(url, site, tmp_path), sleep=sleeps.append)
    poller.poll(DEVICES)
    assert sleeps == [2]  # only the allowed command ran

    commands = requests.get(url + "/debug/summary", timeout=5).json()[
        "sites"][site["site_id"]]["commands"]
    by_type = {c["type"]: c for c in commands}
    assert by_type["restart_device"]["sent"] is True
    assert by_type["restart_device"]["target_mac"] == "aa:bb:cc:dd:ee:01"
    assert by_type["restart_device"]["result"]["status"] == "succeeded"
    assert by_type["restart_device"]["result"]["message"] == (
        "Simulated restart of 192.168.1.20 (aa:bb:cc:dd:ee:01) completed.")
    assert by_type["shell"]["result"]["status"] == "failed"
    assert "Unsupported command type" in by_type["shell"]["result"]["message"]

    poller.poll(DEVICES)  # nothing new
    assert sleeps == [2]


def test_second_result_is_409(start_cloud):
    url = start_cloud()
    site = register(url)
    command_id = queue(url, type="isolate_device", target_mac="aa:bb:cc:dd:ee:01").json()["id"]
    headers = {"Authorization": f"Bearer {site['site_token']}"}
    result = {"status": "succeeded", "finished_at": "2026-11-03T14:31:42+13:00", "message": "m"}
    path = f"{url}/api/agent/commands/{command_id}/result"
    assert requests.post(path, json=result, headers=headers, timeout=5).status_code == 200
    assert requests.post(path, json=result, headers=headers, timeout=5).status_code == 409


def test_commands_and_results_survive_restart(start_cloud, tmp_path):
    state = tmp_path / "state.json"
    url = start_cloud(state)
    site = register(url)
    queue(url, type="reconnect_device", target_mac="aa:bb:cc:dd:ee:01")
    queue(url, type="restart_device", target_mac="aa:bb:cc:dd:ee:01")
    poller = CommandPoller(site_config(url, site, tmp_path / "agent"), sleep=lambda _: None)
    poller.poll(DEVICES)
    queue(url, type="restart_device", target_mac="aa:bb:cc:dd:ee:02")  # not collected yet

    url = start_cloud(state)  # a "restarted" cloud
    commands = requests.get(url + "/debug/summary", timeout=5).json()[
        "sites"][site["site_id"]]["commands"]
    assert [(c["type"], c["sent"], (c["result"] or {}).get("status")) for c in commands] == [
        ("reconnect_device", True, "succeeded"),
        ("restart_device", True, "succeeded"),
        ("restart_device", False, None),
    ]
    headers = {"Authorization": f"Bearer {site['site_token']}"}
    waiting = requests.get(url + "/api/agent/commands", headers=headers, timeout=5).json()
    assert [c["target"]["mac"] for c in waiting["commands"]] == ["aa:bb:cc:dd:ee:02"]


def test_debug_commands_checks_its_body(start_cloud):
    url = start_cloud()
    assert queue(url, type="restart_device", target_mac="aa:bb:cc:dd:ee:01").status_code == 400
    register(url)
    assert queue(url, type="restart_device").status_code == 400
    assert queue(url, type="restart_device", target_mac="aa:bb:cc:dd:ee:01",
                 site_id="site_nope").status_code == 404
