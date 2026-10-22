from pathlib import Path

import pytest
import yaml

from signalbloom_agent.config import ConfigError, load_config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def write_config(tmp_path, **changes):
    """Write the example config with some keys changed (None removes a key)."""
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_example_config_loads():
    config = load_config(EXAMPLE)
    assert config.interval_seconds == 30
    assert config.bandwidth_interval_seconds == 900
    assert config.site_id is None
    assert config.site_token is None


def test_missing_file_gives_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found") as exc_info:
        load_config(tmp_path / "nope.yaml")
    assert "config.example.yaml" in str(exc_info.value)


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"interval_seconds": -5}, "'interval_seconds' must be a whole number above 0"),
        ({"interval_seconds": True}, "'interval_seconds' must be a whole number above 0"),
        ({"cloud_url": "http://example.com"}, "'cloud_url' must start with https://"),
        ({"cloud_url": "http://localhost.evil.com"}, "'cloud_url' must start with https://"),
        ({"cloud_url": None}, "'cloud_url' is missing"),
        ({"setup_code": None, "site_token": None}, "add a 'setup_code'"),
    ],
    ids=["negative-interval", "interval-true", "http-real-host",
         "http-localhost-lookalike", "missing-cloud-url", "no-code-or-token"],
)
def test_bad_config_raises(tmp_path, changes, expected):
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, **changes))
    assert expected in str(exc_info.value)


def test_all_problems_listed_together(tmp_path):
    path = write_config(
        tmp_path,
        interval_seconds=-5,
        bandwidth_interval_seconds=True,
        cloud_url="http://example.com",
        setup_code=None,
        site_token=None,
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    message = str(exc_info.value)
    for expected in (
        "'interval_seconds' must be a whole number above 0",
        "'bandwidth_interval_seconds' must be a whole number above 0",
        "'cloud_url' must start with https://",
        "add a 'setup_code'",
    ):
        assert expected in message


def test_targets_default_when_missing(tmp_path):
    config = load_config(write_config(tmp_path, router_address=None, public_address=None))
    assert config.router_address is None
    assert config.public_address == "1.1.1.1"


def test_targets_from_example():
    config = load_config(EXAMPLE)
    assert config.router_address is None  # blank = find automatically
    assert config.public_address == "1.1.1.1"


@pytest.mark.parametrize("bad", ["-t", "1.1.1.1; rm -rf /", "1.1.1.1 -n 99", "a b"])
def test_bad_targets_rejected(tmp_path, bad):
    with pytest.raises(ConfigError, match="'public_address' must be an IP address or hostname"):
        load_config(write_config(tmp_path, public_address=bad))


def test_localhost_http_allowed(tmp_path):
    config = load_config(write_config(tmp_path, cloud_url="http://localhost:8080"))
    assert config.cloud_url == "http://localhost:8080"


def test_scan_and_bandwidth_size_from_example():
    config = load_config(EXAMPLE)
    assert config.device_scan_seconds == 300
    assert config.device_recheck_seconds == 20
    assert config.bandwidth_test_mb == 25


def test_scan_and_bandwidth_size_default_when_missing(tmp_path):
    config = load_config(write_config(
        tmp_path, device_scan_seconds=None, device_recheck_seconds=None, bandwidth_test_mb=None
    ))
    assert config.device_scan_seconds == 300
    assert config.device_recheck_seconds == 20
    assert config.bandwidth_test_mb == 25


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"device_scan_seconds": 0}, "'device_scan_seconds' must be a whole number above 0"),
        ({"device_scan_seconds": "5m"}, "'device_scan_seconds' must be a whole number above 0"),
        ({"device_recheck_seconds": 0}, "'device_recheck_seconds' must be a whole number above 0"),
        ({"bandwidth_test_mb": -1}, "'bandwidth_test_mb' must be a whole number above 0"),
        ({"bandwidth_test_mb": True}, "'bandwidth_test_mb' must be a whole number above 0"),
        ({"bandwidth_test_mb": 501}, "'bandwidth_test_mb' must be 500 or less"),
    ],
)
def test_bad_scan_or_bandwidth_size_rejected(tmp_path, changes, expected):
    with pytest.raises(ConfigError, match=expected):
        load_config(write_config(tmp_path, **changes))
