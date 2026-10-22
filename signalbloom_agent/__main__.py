"""Entry point: python -m signalbloom_agent"""

import argparse
import sys

from . import __version__
from .config import DEFAULT_PATH, ConfigError, load_config
from .register import RegistrationError, ensure_registered


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
    args = parser.parse_args(argv)

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

    print(f"SignalBloom agent v{__version__}: nothing else to run yet.")
    if args.demo_fault:
        print("--demo-fault is not implemented yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
