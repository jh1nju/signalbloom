"""Fake SignalBloom cloud. FOR LOCAL TESTING ONLY. Standard library only.

Implements the four endpoints in docs/API.md with in-memory data.
Accepts one setup code, TEST-CODE-0001, once.

Run:  python tools/fake_cloud.py [--port 8080] [--with-command]
Then set cloud_url: http://localhost:8080 in config.yaml.
"""

import argparse
import json
import re
import secrets
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SETUP_CODE = "TEST-CODE-0001"
MAX_READINGS = 500
RESULT_PATH = re.compile(r"/api/agent/commands/([^/]+)/result")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class FakeCloud:
    """All state lives here, in memory."""

    def __init__(self, with_command: bool = False):
        self.lock = threading.Lock()
        self.with_command = with_command
        self.code_used = False
        self.sites = {}      # site_token -> site_id
        self.readings = {}   # site_id -> {measured_at: reading}
        self.devices = {}    # site_id -> {mac: device}
        self.commands = {}   # command id -> {"site_id", "command", "sent", "result"}


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeSignalBloom/0.1"

    @property
    def cloud(self) -> FakeCloud:
        return self.server.cloud

    # Routing

    def do_POST(self):
        if self.path == "/api/agent/register":
            return self.register()
        if self.path == "/api/ingest":
            return self.ingest()
        match = RESULT_PATH.fullmatch(self.path)
        if match:
            return self.command_result(match.group(1))
        self.send_error_json(404, "not_found", "No such endpoint.")

    def do_GET(self):
        if self.path == "/api/agent/commands":
            return self.list_commands()
        self.send_error_json(404, "not_found", "No such endpoint.")

    # Helpers

    def send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: int, code: str, message: str) -> None:
        self.send_json(status, {"error": {"code": code, "message": message}})

    def read_json(self):
        """Return the body as a dict, or send 400 and return None."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"null")
        except (ValueError, json.JSONDecodeError):
            body = None
        if not isinstance(body, dict):
            self.send_error_json(400, "invalid_body", "Body must be a JSON object.")
            return None
        return body

    def authenticated_site(self):
        """Return the caller's site_id, or send 401 and return None."""
        header = self.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else None
        with self.cloud.lock:
            site_id = self.cloud.sites.get(token)
        if site_id is None:
            self.send_error_json(401, "invalid_token", "Site token is missing or not valid.")
        return site_id

    # 1. Register

    def register(self):
        body = self.read_json()
        if body is None:
            return
        if not all(isinstance(body.get(k), str) for k in ("setup_code", "agent_version", "hostname")):
            return self.send_error_json(
                400, "invalid_body", "setup_code, agent_version and hostname are required."
            )
        if body["setup_code"] != SETUP_CODE:
            return self.send_error_json(404, "code_not_found", "Setup code not found.")
        with self.cloud.lock:
            if self.cloud.code_used:
                return self.send_error_json(
                    410, "code_used_or_expired", "Setup code has already been used or has expired."
                )
            self.cloud.code_used = True
            site_id = "site_" + secrets.token_hex(3)
            site_token = "sbt_test_" + secrets.token_hex(16)
            self.cloud.sites[site_token] = site_id
            if self.cloud.with_command:
                command_id = "cmd_" + secrets.token_hex(2)
                self.cloud.commands[command_id] = {
                    "site_id": site_id,
                    "sent": False,
                    "result": None,
                    "command": {
                        "id": command_id,
                        "type": "restart_device",
                        "target": {"mac": "aa:bb:cc:dd:ee:01"},
                        "approved_at": now(),
                        "simulated": True,
                    },
                }
        print(f"Registered {site_id} for host {body['hostname']!r}", flush=True)  # never the token
        self.send_json(201, {
            "site_id": site_id,
            "site_token": site_token,
            "interval_seconds": 30,
            "bandwidth_interval_seconds": 900,
        })

    # 2. Ingest

    def ingest(self):
        site_id = self.authenticated_site()
        if site_id is None:
            return
        body = self.read_json()
        if body is None:
            return
        readings = body.get("readings")
        if not isinstance(readings, list) or not all(
            isinstance(r, dict) and isinstance(r.get("measured_at"), str) for r in readings
        ):
            return self.send_error_json(
                400, "invalid_body", "readings must be a list of objects with measured_at."
            )
        if body.get("site_id") != site_id:
            return self.send_error_json(403, "site_mismatch", "Token belongs to another site.")
        if len(readings) > MAX_READINGS:
            return self.send_error_json(
                413, "too_many_readings", f"Send at most {MAX_READINGS} readings per request."
            )
        accepted = duplicates = 0
        with self.cloud.lock:
            stored = self.cloud.readings.setdefault(site_id, {})
            for reading in readings:
                if reading["measured_at"] in stored:
                    duplicates += 1
                else:
                    stored[reading["measured_at"]] = reading
                    accepted += 1
            devices = self.cloud.devices.setdefault(site_id, {})
            for device in body.get("devices") or []:
                if isinstance(device, dict) and "mac" in device:
                    devices[device["mac"]] = device
        self.send_json(202, {"accepted": accepted, "duplicates": duplicates})

    # 3. Commands

    def list_commands(self):
        site_id = self.authenticated_site()
        if site_id is None:
            return
        with self.cloud.lock:
            waiting = [
                c for c in self.cloud.commands.values()
                if c["site_id"] == site_id and not c["sent"]
            ]
            for c in waiting:
                c["sent"] = True
        self.send_json(200, {"commands": [c["command"] for c in waiting]})

    # 4. Command result

    def command_result(self, command_id: str):
        site_id = self.authenticated_site()
        if site_id is None:
            return
        body = self.read_json()
        if body is None:
            return
        with self.cloud.lock:
            command = self.cloud.commands.get(command_id)
            if command is None or command["site_id"] != site_id:
                return self.send_error_json(404, "command_not_found", "Command not found.")
            if command["result"] is not None:
                return self.send_error_json(
                    409, "result_already_recorded", "A result was already recorded."
                )
            if body.get("status") not in ("succeeded", "failed") or not isinstance(
                body.get("finished_at"), str
            ):
                return self.send_error_json(
                    400, "invalid_body", "status (succeeded/failed) and finished_at are required."
                )
            command["result"] = body
        print(f"Result for {command_id}: {body['status']}", flush=True)
        self.send_json(200, {"recorded": True})


class IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def make_server(port: int = 8080, with_command: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.cloud = FakeCloud(with_command=with_command)
    return server


def make_ipv6_twin(server: ThreadingHTTPServer, port: int):
    """Also listen on ::1, sharing the same data.

    On Windows, "localhost" tries ::1 first and waits ~2 s if nothing answers.
    Returns None if IPv6 isn't available.
    """
    try:
        twin = IPv6Server(("::1", port), Handler)
    except OSError:
        return None
    twin.cloud = server.cloud
    return twin


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake SignalBloom cloud (testing only).")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--with-command", action="store_true",
        help="queue one simulated restart_device command for each new site",
    )
    args = parser.parse_args()
    server = make_server(args.port, args.with_command)
    twin = make_ipv6_twin(server, args.port)
    if twin:
        threading.Thread(target=twin.serve_forever, daemon=True).start()
    print(f"Fake SignalBloom cloud on http://localhost:{args.port} (testing only)", flush=True)
    print(f"Setup code: {SETUP_CODE} (works once). Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if twin:
            twin.shutdown()
            twin.server_close()


if __name__ == "__main__":
    main()
