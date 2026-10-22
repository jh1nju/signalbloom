"""Entry point: python -m signalbloom_agent"""

import argparse
import sys

from . import __version__
from .config import ConfigError, load_config


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
        load_config()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"SignalBloom agent v{__version__} skeleton: nothing to run yet.")
    if args.demo_fault:
        print("--demo-fault is not implemented yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
