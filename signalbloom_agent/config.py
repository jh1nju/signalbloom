"""Load and check the agent's config.yaml."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

DEFAULT_PATH = "config.yaml"
DEFAULT_PUBLIC_ADDRESS = "1.1.1.1"
DEFAULT_DEVICE_SCAN_SECONDS = 300
DEFAULT_DEVICE_RECHECK_SECONDS = 20
DEFAULT_BANDWIDTH_TEST_MB = 25
MAX_BANDWIDTH_TEST_MB = 500
DEFAULT_DEMO_FAULT_LATENCY_MS = 300
DEFAULT_DEMO_FAULT_LOSS_PCT = 20

# Hostnames and IPv4/IPv6 addresses. Must not start with "-", so a value like
# "-t" can't be passed to ping as an option.
_TARGET = re.compile(r"^[A-Za-z0-9:][A-Za-z0-9.:\-]*$")


class ConfigError(Exception):
    """The config file is missing or not valid."""


@dataclass(frozen=True)
class Config:
    cloud_url: str
    setup_code: str | None
    site_id: str | None
    site_token: str | None
    interval_seconds: int
    bandwidth_interval_seconds: int
    data_dir: Path
    router_address: str | None = None  # None = find it automatically
    public_address: str = DEFAULT_PUBLIC_ADDRESS
    device_scan_seconds: int = DEFAULT_DEVICE_SCAN_SECONDS
    device_recheck_seconds: int = DEFAULT_DEVICE_RECHECK_SECONDS
    bandwidth_test_mb: int = DEFAULT_BANDWIDTH_TEST_MB
    demo_fault_latency_ms: int = DEFAULT_DEMO_FAULT_LATENCY_MS
    demo_fault_loss_pct: int = DEFAULT_DEMO_FAULT_LOSS_PCT

    def __repr__(self) -> str:
        # Never show the site token, so it can't end up in logs (docs/API.md).
        token = "<set>" if self.site_token else None
        return (
            f"Config(cloud_url={self.cloud_url!r}, site_id={self.site_id!r}, "
            f"site_token={token!r}, interval_seconds={self.interval_seconds}, "
            f"bandwidth_interval_seconds={self.bandwidth_interval_seconds}, "
            f"data_dir={str(self.data_dir)!r}, "
            f"router_address={self.router_address!r}, "
            f"public_address={self.public_address!r}, "
            f"device_scan_seconds={self.device_scan_seconds}, "
            f"device_recheck_seconds={self.device_recheck_seconds}, "
            f"bandwidth_test_mb={self.bandwidth_test_mb}, "
            f"demo_fault_latency_ms={self.demo_fault_latency_ms}, "
            f"demo_fault_loss_pct={self.demo_fault_loss_pct})"
        )


def _validate(raw: dict, path: Path) -> None:
    """Raise ConfigError if raw is missing keys or has bad values."""
    problems = []
    for key in ("cloud_url", "interval_seconds", "bandwidth_interval_seconds", "data_dir"):
        if raw.get(key) in (None, ""):
            problems.append(f"'{key}' is missing")
    for key in ("interval_seconds", "bandwidth_interval_seconds",
                "device_scan_seconds", "device_recheck_seconds", "bandwidth_test_mb"):
        v = raw.get(key)
        if v not in (None, "") and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            problems.append(f"'{key}' must be a whole number above 0")
    mb = raw.get("bandwidth_test_mb")
    if isinstance(mb, int) and not isinstance(mb, bool) and mb > MAX_BANDWIDTH_TEST_MB:
        problems.append(f"'bandwidth_test_mb' must be {MAX_BANDWIDTH_TEST_MB} or less")
    for key in ("demo_fault_latency_ms", "demo_fault_loss_pct"):
        v = raw.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            problems.append(f"'{key}' must be a whole number, 0 or more")
    loss = raw.get("demo_fault_loss_pct")
    if isinstance(loss, int) and not isinstance(loss, bool) and loss > 100:
        problems.append("'demo_fault_loss_pct' must be 100 or less")
    url = str(raw.get("cloud_url") or "")
    parsed = urlparse(url)
    local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if url and parsed.scheme != "https" and not local:
        problems.append("'cloud_url' must start with https://")
    if not raw.get("site_token") and not raw.get("setup_code"):
        problems.append("add a 'setup_code' from the dashboard to register this site")
    for key in ("router_address", "public_address"):
        v = raw.get(key)
        if v not in (None, "") and not (isinstance(v, str) and _TARGET.match(v)):
            problems.append(f"'{key}' must be an IP address or hostname")
    if problems:
        raise ConfigError(f"{path}: " + "; ".join(problems))


def load_config(path: str | Path = DEFAULT_PATH) -> Config:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}. "
            "Copy config.example.yaml to config.yaml and fill it in."
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain key: value settings.")

    _validate(raw, path)

    return Config(
        cloud_url=raw["cloud_url"],
        setup_code=raw.get("setup_code"),
        site_id=raw.get("site_id"),
        site_token=raw.get("site_token"),
        interval_seconds=raw["interval_seconds"],
        bandwidth_interval_seconds=raw["bandwidth_interval_seconds"],
        data_dir=Path(raw["data_dir"]),
        router_address=raw.get("router_address") or None,
        public_address=raw.get("public_address") or DEFAULT_PUBLIC_ADDRESS,
        device_scan_seconds=raw.get("device_scan_seconds") or DEFAULT_DEVICE_SCAN_SECONDS,
        device_recheck_seconds=raw.get("device_recheck_seconds") or DEFAULT_DEVICE_RECHECK_SECONDS,
        bandwidth_test_mb=raw.get("bandwidth_test_mb") or DEFAULT_BANDWIDTH_TEST_MB,
        # Explicit None checks: 0 is a valid amount here.
        demo_fault_latency_ms=_or_default(raw, "demo_fault_latency_ms",
                                          DEFAULT_DEMO_FAULT_LATENCY_MS),
        demo_fault_loss_pct=_or_default(raw, "demo_fault_loss_pct",
                                        DEFAULT_DEMO_FAULT_LOSS_PCT),
    )


def _or_default(raw: dict, key: str, default):
    v = raw.get(key)
    return default if v is None else v
