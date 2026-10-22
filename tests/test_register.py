import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from signalbloom_agent.__main__ import main
from signalbloom_agent.config import load_config
from signalbloom_agent.register import RegistrationError, ensure_registered

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"
TOKEN = "sbt_test_SECRET_do_not_print_123"
POST = "signalbloom_agent.register.requests.post"


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.yaml"
    shutil.copy(EXAMPLE, path)
    return path


def fake_response(status, payload):
    response = MagicMock(status_code=status, reason="")
    response.json.return_value = payload
    return response


SUCCESS = fake_response(201, {
    "site_id": "site_8f3a2c",
    "site_token": TOKEN,
    "interval_seconds": 30,
    "bandwidth_interval_seconds": 900,
})


def no_sleep(_seconds):
    pass


def test_success_saves_token_and_keeps_rest(config_path):
    with patch(POST, return_value=SUCCESS) as post:
        config = ensure_registered(load_config(config_path), config_path, sleep=no_sleep)

    sent = post.call_args.kwargs["json"]
    assert sent["setup_code"] == "SB-XXXX-XXXX"
    assert set(sent) == {"setup_code", "agent_version", "hostname"}
    assert post.call_args.args[0].endswith("/api/agent/register")

    saved = load_config(config_path)
    assert saved.site_id == config.site_id == "site_8f3a2c"
    assert saved.site_token == TOKEN
    assert saved.setup_code is None
    assert saved.interval_seconds == 30
    text = config_path.read_text(encoding="utf-8")
    assert "# One-time setup code" in text  # comments survive
    assert TOKEN not in repr(config)


def test_existing_token_skips_registration(config_path):
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("site_token:", f"site_token: {TOKEN}"), encoding="utf-8")
    with patch(POST) as post:
        config = ensure_registered(load_config(config_path), config_path, sleep=no_sleep)
    post.assert_not_called()
    assert config.site_token == TOKEN


@pytest.mark.parametrize("status, code", [(404, "code_not_found"), (410, "code_used_or_expired")])
def test_bad_code_asks_for_new_one(config_path, status, code):
    before = config_path.read_text(encoding="utf-8")
    response = fake_response(status, {"error": {"code": code, "message": "x"}})
    with patch(POST, return_value=response) as post:
        with pytest.raises(RegistrationError, match="new setup code"):
            ensure_registered(load_config(config_path), config_path, sleep=no_sleep)
    assert post.call_count == 1  # 410 is final: no retry
    assert config_path.read_text(encoding="utf-8") == before


def test_network_failure_retries_then_gives_up(config_path):
    sleep = MagicMock()
    with patch(POST, side_effect=requests.ConnectionError("down")) as post:
        with pytest.raises(RegistrationError, match="Could not reach"):
            ensure_registered(load_config(config_path), config_path, sleep=sleep)
    assert post.call_count == 4
    waits = [c.args[0] for c in sleep.call_args_list]
    assert len(waits) == 3
    assert all(w > 0 for w in waits)
    assert waits == sorted(waits) and len(set(waits)) == 3  # strictly increasing


def test_network_recovers_after_retry(config_path):
    with patch(POST, side_effect=[requests.Timeout(), requests.ConnectionError(), SUCCESS]) as post:
        config = ensure_registered(load_config(config_path), config_path, sleep=no_sleep)
    assert post.call_count == 3
    assert config.site_token == TOKEN


def test_main_never_prints_token(config_path, monkeypatch, capsys, caplog):
    monkeypatch.chdir(config_path.parent)
    with patch(POST, return_value=SUCCESS):
        assert main([]) == 0
        assert main([]) == 0  # second run: already registered
    out = capsys.readouterr()
    assert "Registered as site site_8f3a2c" in out.out
    assert "Already registered" in out.out
    assert TOKEN not in out.out + out.err
    assert TOKEN not in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_saved_config_is_owner_only(config_path):
    with patch(POST, return_value=SUCCESS):
        ensure_registered(load_config(config_path), config_path, sleep=no_sleep)
    assert os.stat(config_path).st_mode & 0o777 == 0o600
