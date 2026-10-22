"""Entry point: python -m signalbloom_agent"""

import argparse
import json
import sys
from datetime import datetime

from .agent import run, setup_logging
from .collectors import CollectorError
from .collectors.bandwidth import measure_download_mbps
from .collectors.devices import DeviceScanner
from .collectors.gateway import find_default_gateway
from .collectors.ping import ping
from .collectors.uptime import internet_up
from .config import DEFAULT_PATH, Config, ConfigError, load_config
from .register import RegistrationError, ensure_registered
from .sender import AuthError


def collect_once(config: Config) -> int:
    """Run the collectors once, print an ingest-shaped body (docs/API.md), exit."""
    try:
        public = ping(config.public_address)
    except CollectorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    reading = {
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "latency_ms": public["latency_ms"],
        "packet_loss_pct": public["packet_loss_pct"],
        "internet_up": internet_up(config.public_address, public),
        "download_mbps": None,  # filled in below
        "upload_mbps": None,
        "error_rate_pct": None,
    }

    print(f"Testing download speed ({config.bandwidth_test_mb} MB)...", file=sys.stderr)
    reading["download_mbps"] = measure_download_mbps(config.bandwidth_test_mb)

    print("Scanning the local network for devices (up to 30 s)...", file=sys.stderr)
    devices = DeviceScanner(config.public_address, config.device_scan_seconds).devices()

    print(json.dumps({"readings": [reading], "devices": devices}, indent=2))

    # The router result isn't part of the API reading, so it goes to stderr.
    router = config.router_address or find_default_gateway()
    if router is None:
        print("Router: not found (set router_address in config.yaml)", file=sys.stderr)
    else:
        local = ping(router)
        latency = "no reply" if local["latency_ms"] is None else f"{local['latency_ms']} ms"
        print(
            f"Router {router} (local only): {latency}, {local['packet_loss_pct']}% loss",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="signalbloom_agent",
        description="SignalBloom local agent: sends network readings to the SignalBloom cloud.",
    )
    parser.add_argument(
        "--demo-fault",
        action="store_true",
        help="inject a fake network fault for demos (not implemented yet)",
    )
    parser.add_argument(
        "--collect-once",
        action="store_true",
        help="measure once, print the reading and exit (nothing is sent)",
    )
    args = parser.parse_args(argv)

    if args.collect_once:
        try:
            config = load_config(DEFAULT_PATH)
        except ConfigError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return collect_once(config)

    try:
        config = load_config(DEFAULT_PATH)
        already_registered = bool(config.site_token)
        config = ensure_registered(config, DEFAULT_PATH)
    except (ConfigError, RegistrationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if already_registered:
        print(f"Already registered as site {config.site_id}.")
    else:
        print(f"Registered as site {config.site_id}.")

    if args.demo_fault:
        print("--demo-fault is not implemented yet.")

    setup_logging(config.data_dir)
    try:
        run(config)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    except (AuthError, CollectorError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
