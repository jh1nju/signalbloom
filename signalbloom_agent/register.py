"""Register this agent with the SignalBloom cloud (docs/API.md section 1)."""

import dataclasses
import getpass
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from . import __version__
from .config import Config

TRIES = 4
TIMEOUT_SECONDS = 10

NEW_CODE_MESSAGE = (
    "This setup code isn't valid, or it has already been used or has expired. "
    "Get a new setup code from Account & Settings in the SignalBloom dashboard, "
    "put it in config.yaml as setup_code, and start the agent again."
)


class RegistrationError(Exception):
    """Registration failed. The message is safe to show to the user."""


def retry_wait(attempt: int) -> float:
    """Seconds to wait after failed try number `attempt` (1, 2 or 3)."""
    return min(2 ** (attempt - 1), 30)


def ensure_registered(config: Config, path: str | Path, *, sleep=time.sleep) -> Config:
    """Register if there's no site token yet. Returns the (possibly updated) config."""
    if config.site_token:
        return config

    url = config.cloud_url.rstrip("/") + "/api/agent/register"
    body = {
        "setup_code": config.setup_code,
        "agent_version": __version__,
        "hostname": socket.gethostname(),
    }
    response = _post_with_retries(url, body, config.cloud_url, sleep)

    if response.status_code == 201:
        try:
            data = response.json()
            site_id, site_token = data["site_id"], data["site_token"]
        except (ValueError, KeyError, TypeError):
            # Don't include the body: it may contain the token.
            raise RegistrationError(
                "The cloud accepted the setup code but sent back a reply the agent "
                "couldn't read. Get a new setup code from the dashboard and try again."
            ) from None
        save_registration(path, site_id, site_token)
        return dataclasses.replace(
            config, site_id=site_id, site_token=site_token, setup_code=None
        )

    if response.status_code in (404, 410):
        raise RegistrationError(NEW_CODE_MESSAGE)

    raise RegistrationError(
        f"Registration failed (HTTP {response.status_code}): {_error_message(response)}"
    )


def _post_with_retries(url, body, cloud_url, sleep):
    for attempt in range(1, TRIES + 1):
        try:
            return requests.post(url, json=body, timeout=TIMEOUT_SECONDS)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == TRIES:
                raise RegistrationError(
                    f"Could not reach the SignalBloom cloud at {cloud_url} after "
                    f"{TRIES} tries. Check the internet connection and cloud_url "
                    "in config.yaml."
                ) from exc
            wait = retry_wait(attempt)
            print(
                f"Could not reach the cloud (try {attempt} of {TRIES}). "
                f"Retrying in {wait:g} s...",
                file=sys.stderr,
            )
            sleep(wait)


def _error_message(response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.reason or "no details"


def save_registration(path: str | Path, site_id: str, site_token: str) -> None:
    """Write site_id and site_token into config.yaml, clear setup_code, keep the rest.

    Lines are edited in place so the file's comments survive.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    for key, value in (("setup_code", None), ("site_id", site_id), ("site_token", site_token)):
        # json.dumps gives a double-quoted string, which is also valid YAML.
        line = f"{key}: {json.dumps(value)}" if value else f"{key}:"
        pattern = re.compile(rf"^{key}:.*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(lambda _m: line, text, count=1)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"

    # Write to a temp file, lock it down, then swap it in, so a crash can't
    # leave a half-written config or a moment where others can read the token.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        _restrict_to_owner(tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _restrict_to_owner(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    user = getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    account = f"{domain}\\{user}" if domain else user
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        print(
            "Warning: could not limit config.yaml to your user account. "
            "Registration still worked.",
            file=sys.stderr,
        )
