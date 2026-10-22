from unittest.mock import MagicMock, patch

import requests

from signalbloom_agent.collectors import CollectorError
from signalbloom_agent.collectors.uptime import CHECK_URL, internet_up

PING = "signalbloom_agent.collectors.uptime.ping"
GET = "signalbloom_agent.collectors.uptime.requests.get"

REPLIED = {"latency_ms": 5.0, "packet_loss_pct": 75.0}  # one reply is enough
NO_REPLY = {"latency_ms": None, "packet_loss_pct": 100.0}


def test_ping_reply_means_up_without_https():
    with patch(PING, return_value=REPLIED) as ping, patch(GET) as get:
        assert internet_up("1.1.1.1") is True
    ping.assert_called_once_with("1.1.1.1")
    get.assert_not_called()


def test_earlier_ping_result_is_reused():
    with patch(PING) as ping, patch(GET) as get:
        assert internet_up("1.1.1.1", REPLIED) is True
    ping.assert_not_called()
    get.assert_not_called()


def test_ping_fails_but_https_works():
    with patch(PING, return_value=NO_REPLY), patch(
        GET, return_value=MagicMock(status_code=204)
    ) as get:
        assert internet_up("1.1.1.1") is True
    assert get.call_args.args[0] == CHECK_URL
    assert get.call_args.kwargs["timeout"] == 3
    assert get.call_args.kwargs["allow_redirects"] is False


def test_both_fail_means_down():
    with patch(PING, return_value=NO_REPLY), patch(GET, side_effect=requests.Timeout()):
        assert internet_up("1.1.1.1") is False


def test_redirect_means_down():
    # A captive portal or proxy answering with a redirect isn't the real internet.
    with patch(GET, return_value=MagicMock(status_code=302)):
        assert internet_up("1.1.1.1", NO_REPLY) is False


def test_missing_ping_falls_back_to_https():
    with patch(PING, side_effect=CollectorError("no ping")), patch(
        GET, return_value=MagicMock(status_code=204)
    ):
        assert internet_up("1.1.1.1") is True
