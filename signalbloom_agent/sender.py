"""Send readings to the cloud (docs/API.md section 2: POST /api/ingest).

Every reading goes into the buffer first. flush() sends the buffer oldest
first, up to MAX_BATCH per request, and removes readings only after the cloud
answers 202. Batches are sent in order, so accepted readings are always at the
front of the buffer.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import requests

from .buffer import ReadingBuffer
from .config import Config

MAX_BATCH = 500
TIMEOUT_SECONDS = 10
REJECTED_FILE = "rejected.jsonl"

REREGISTER = (
    "Get a new setup code from Account & Settings in the SignalBloom dashboard, "
    "put it in config.yaml as setup_code, clear site_token, and start the agent again. "
    "Readings and fix results not sent yet are kept and will be sent after registering."
)


class AuthError(Exception):
    """The cloud refused the site token (401/403). The message is safe to show."""


class Reply(NamedTuple):
    status: int
    message: str  # the cloud's error message, "" on success


class _Stop(Exception):
    """Stop flushing for now; the readings stay buffered. Message is for the log."""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


@dataclass
class SendSummary:
    accepted: int = 0
    duplicates: int = 0
    requests: int = 0       # requests that got a 202
    buffered: int = 0       # readings still waiting after this flush
    rejected: list = field(default_factory=list)  # (measured_at, status, message)
    devices_rejected: Reply | None = None  # the cloud refused the device list
    devices_restored: bool = False         # a new full scan ended the pause
    error: str | None = None

    def lines(self) -> list[str]:
        """Short log lines describing this flush."""
        out = []
        if self.devices_restored:
            out.append("new device scan, sending the device list again")
        if self.devices_rejected:
            status, message = self.devices_rejected
            detail = f"HTTP {status}: {message}" if message else f"HTTP {status}"
            out.append(f"cloud rejected the device list ({detail}), saved to {REJECTED_FILE}; "
                       "sending readings without devices until the next full scan")
        for measured_at, status, message in self.rejected:
            detail = f"HTTP {status}: {message}" if message else f"HTTP {status}"
            out.append(f"rejected reading from {measured_at} ({detail}), "
                       f"saved to {REJECTED_FILE}")
        sent = self.accepted + self.duplicates
        if sent:
            via = f" in {self.requests} requests" if self.requests > 1 else ""
            out.append(f"sent {_plural(sent, 'reading')}{via}, "
                       f"{_plural(self.duplicates, 'duplicate')}")
        if self.error:
            out.append(f"{self.error}, {_plural(self.buffered, 'reading')} buffered")
        return out


class Sender:
    def __init__(self, config: Config, buffer: ReadingBuffer, *,
                 post=requests.post, now=now_iso):
        self._url = config.cloud_url.rstrip("/") + "/api/ingest"
        self._site_id = config.site_id
        self._headers = {"Authorization": f"Bearer {config.site_token}"}
        self._buffer = buffer
        self._rejected_path = Path(config.data_dir) / REJECTED_FILE
        self._post_fn = post
        self._now = now
        # While the cloud refuses our device list, send [] instead, until a
        # full scan newer than the one the bad list came from.
        self._devices_paused = False
        self._paused_scan = None
        self._scan = None

    def __repr__(self) -> str:
        return f"Sender(url={self._url!r}, site_id={self._site_id!r})"  # never the token

    def flush(self, devices: list[dict], scan=None) -> SendSummary:
        """Send everything in the buffer. Raises AuthError on 401/403.

        `scan` identifies the full scan `devices` came from, so a device list
        the cloud refused is left out only until the next full scan.
        """
        summary = SendSummary()
        self._scan = scan
        if self._devices_paused and scan != self._paused_scan:
            self._devices_paused = False
            summary.devices_restored = True
        try:
            while len(self._buffer):
                before = len(self._buffer)
                self._send_part(self._buffer.oldest(MAX_BATCH), devices, summary)
                if len(self._buffer) >= before:
                    break  # nothing was removed; don't loop forever
        except _Stop as stop:
            summary.error = str(stop)
        summary.buffered = len(self._buffer)
        return summary

    def _send_part(self, readings: list[dict], devices: list[dict],
                   summary: SendSummary) -> None:
        """Send `readings` (always the oldest ones in the buffer) until each is
        accepted or rejected.

        self._post(readings, devices, summary) sends one request and returns a
        Reply. On 202 it has already removed `readings` from the buffer and
        counted them. On 401/403 it raises AuthError, and on a network error it
        raises _Stop, so neither reaches this function.

        self._reject(reading, reply, summary) saves one reading to
        rejected.jsonl and removes it from the front of the buffer.

        Raise _Stop("cloud error (HTTP 503)") (with the real status) to stop
        for now and keep what's left buffered.

        A single reading that gets 400 is first retried without devices. If
        that works, the device list was the problem, not the reading.
        """
        reply = self._post(readings, devices, summary)
        if reply.status == 202:
            return
        if reply.status in (400, 413):
            if len(readings) == 1:
                if reply.status == 400 and devices and not self._devices_paused:
                    retry = self._post(readings, [], summary)
                    if retry.status == 202:
                        self._pause_devices(devices, reply, summary)
                        return
                    if retry.status not in (400, 413):
                        raise _Stop(f"cloud error (HTTP {retry.status})")
                    reply = retry
                self._reject(readings[0], reply, summary)
                return
            mid = len(readings) // 2
            self._send_part(readings[:mid], devices, summary)
            self._send_part(readings[mid:], devices, summary)
            return
        raise _Stop(f"cloud error (HTTP {reply.status})")

    def _post(self, readings: list[dict], devices: list[dict], summary: SendSummary) -> Reply:
        body = {
            "site_id": self._site_id,
            "sent_at": self._now(),
            "readings": readings,
            "devices": [] if self._devices_paused else devices,
        }
        try:
            response = self._post_fn(self._url, json=body, headers=self._headers,
                                     timeout=TIMEOUT_SECONDS)
        except requests.RequestException:
            raise _Stop("cloud unreachable") from None

        status = response.status_code
        if status == 401:
            raise AuthError("The cloud no longer accepts this agent's site token (HTTP 401). "
                            + REREGISTER)
        if status == 403:
            raise AuthError("The cloud says this site token belongs to another site "
                            "(HTTP 403). " + REREGISTER)
        if status == 202:
            counts = _json(response)
            duplicates = counts.get("duplicates")
            accepted = counts.get("accepted")
            if not isinstance(duplicates, int):
                duplicates = 0
            if not isinstance(accepted, int):
                accepted = len(readings) - duplicates
            summary.accepted += accepted
            summary.duplicates += duplicates
            summary.requests += 1
            self._buffer.remove_oldest(len(readings))
            return Reply(status, "")
        return Reply(status, _error_message(response))

    def _reject(self, reading: dict, reply: Reply, summary: SendSummary) -> None:
        self._save_rejected({"reading": reading}, reply)
        self._buffer.remove_oldest(1)
        summary.rejected.append((reading.get("measured_at"), reply.status, reply.message))

    def _pause_devices(self, devices: list[dict], reply: Reply, summary: SendSummary) -> None:
        self._save_rejected({"devices": devices}, reply)
        self._devices_paused = True
        self._paused_scan = self._scan
        summary.devices_rejected = reply

    def _save_rejected(self, item: dict, reply: Reply) -> None:
        record = {"rejected_at": self._now(), "status": reply.status,
                  "message": reply.message, **item}
        with open(self._rejected_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def _json(response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_message(response) -> str:
    try:
        return str(_json(response)["error"]["message"])
    except (KeyError, TypeError):
        return ""
