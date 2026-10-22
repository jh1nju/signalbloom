"""Decide whether the internet is up."""

import requests

from . import CollectorError
from .ping import ping

# Run by a different company than 1.1.1.1 (Cloudflare), so one provider being
# down doesn't look like the whole internet being down. HTTPS also means a
# captive login page can't fake a success.
CHECK_URL = "https://www.gstatic.com/generate_204"
CHECK_TIMEOUT_SECONDS = 3


def internet_up(public_target: str, ping_result: dict | None = None) -> bool:
    """True if a ping to public_target gets any reply, or the HTTPS check succeeds.

    Pass the result of an earlier ping to public_target to avoid pinging twice.
    """
    if ping_result is None:
        try:
            ping_result = ping(public_target)
        except CollectorError:
            ping_result = None
    if ping_result is not None and ping_result["packet_loss_pct"] < 100:
        return True

    try:
        response = requests.get(
            CHECK_URL, timeout=CHECK_TIMEOUT_SECONDS, allow_redirects=False
        )
    except requests.RequestException:
        return False
    return 200 <= response.status_code < 300
