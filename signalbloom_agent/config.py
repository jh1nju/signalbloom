"""Load and check the agent's config.yaml."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

DEFAULT_PATH = "config.yaml"


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

    def __repr__(self) -> str:
        # Never show the site token, so it can't end up in logs (docs/API.md).
        token = "<set>" if self.site_token else None
        return (
            f"Config(cloud_url={self.cloud_url!r}, site_id={self.site_id!r}, "
            f"site_token={token!r}, interval_seconds={self.interval_seconds}, "
            f"bandwidth_interval_seconds={self.bandwidth_interval_seconds}, "
            f"data_dir={str(self.data_dir)!r})"
        )


def _validate(raw: dict, path: Path) -> None:
    """Raise ConfigError if raw is missing keys or has bad values."""
    problems = []
    for key in ("cloud_url", "interval_seconds", "bandwidth_interval_seconds", "data_dir"):
        if raw.get(key) in (None, ""):
            problems.append(f"'{key}' is missing")
    for key in ("interval_seconds", "bandwidth_interval_seconds"):
        v = raw.get(key)
        if v not in (None, "") and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            problems.append(f"'{key}' must be a whole number above 0")
    url = str(raw.get("cloud_url") or "")
    parsed = urlparse(url)
    local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if url and parsed.scheme != "https" and not local:
        problems.append("'cloud_url' must start with https://")
    if not raw.get("site_token") and not raw.get("setup_code"):
        problems.append("add a 'setup_code' from the dashboard to register this site")
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
    )
