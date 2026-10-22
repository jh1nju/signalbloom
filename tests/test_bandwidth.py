from unittest.mock import MagicMock, patch

import pytest
import requests

from signalbloom_agent.collectors import bandwidth
from signalbloom_agent.collectors.bandwidth import BandwidthTest, measure_download_mbps

GET = "signalbloom_agent.collectors.bandwidth.requests.get"
CLOCK = "signalbloom_agent.collectors.bandwidth.time.monotonic"


class FakeResponse:
    def __init__(self, chunks, status_code=200):
        self.chunks = chunks
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        yield from self.chunks


def ticking_clock(step):
    """A clock that moves forward `step` seconds every time it is read."""
    state = {"now": 0.0}

    def clock():
        state["now"] += step
        return state["now"]
    return clock


def test_speed_from_timed_bytes():
    # 1 MB test: 10 chunks of 100 kB. The first chunk starts the timer, so 9
    # chunks (900,000 bytes) are timed. Each clock read adds 0.1 s: 9 reads in
    # the loop plus 1 at the end = 1.0 s -> 900,000 * 8 / 1.0 / 1e6 = 7.2 Mbps.
    response = FakeResponse([b"x" * 100_000] * 10)
    with patch(GET, return_value=response) as get, patch(CLOCK, ticking_clock(0.1)):
        assert measure_download_mbps(1) == 7.2

    url = get.call_args.args[0]
    assert url.startswith("https://") and url.endswith("bytes=1000000")
    assert get.call_args.kwargs["stream"] is True
    assert get.call_args.kwargs["headers"] == {"Accept-Encoding": "identity"}


def test_short_body_is_a_failure():
    response = FakeResponse([b"x" * 100_000] * 5)  # half of 1 MB
    with patch(GET, return_value=response), patch(CLOCK, ticking_clock(0.1)):
        assert measure_download_mbps(1) is None


def test_slow_link_stops_at_time_limit_and_reports():
    response = FakeResponse([b"x" * 100_000] * 1000)  # would be 100 MB
    with patch(GET, return_value=response), patch(CLOCK, ticking_clock(1.0)), \
            patch.object(bandwidth, "MAX_SECONDS", 5):
        mbps = measure_download_mbps(100)
    # Stops after 5 timed chunks over ~6 s: a real, low number rather than None.
    assert mbps == pytest.approx(500_000 * 8 / 6 / 1e6, abs=0.1)


def test_http_error_is_none():
    with patch(GET, return_value=FakeResponse([], status_code=500)):
        assert measure_download_mbps(1) is None


def test_network_error_is_none():
    with patch(GET, side_effect=requests.ConnectionError("down")):
        assert measure_download_mbps(1) is None


def test_empty_body_is_none():
    with patch(GET, return_value=FakeResponse([])):
        assert measure_download_mbps(1) is None


def test_bandwidth_test_runs_only_when_due():
    now = {"t": 0.0}
    measure = MagicMock(return_value=87.3)
    test = BandwidthTest(25, 900, clock=lambda: now["t"], measure=measure)

    assert test.maybe_run() == 87.3  # first call always runs
    now["t"] = 899
    assert test.maybe_run() is None
    now["t"] = 900
    assert test.maybe_run() == 87.3
    assert measure.call_count == 2
    measure.assert_called_with(25)
