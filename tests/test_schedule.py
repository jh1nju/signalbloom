import threading

from signalbloom_agent.schedule import Ticker, wait_until_due


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_first_tick_is_immediate():
    clock = FakeClock()
    assert Ticker(30, clock).seconds_until_due() == 0


def test_no_drift_when_work_takes_time():
    clock = FakeClock(1000.0)
    ticker = Ticker(30, clock)
    starts = []
    for _ in range(4):
        starts.append(clock.t)
        clock.t += 4.7  # the work (ping + send) takes a while
        assert ticker.advance() == 0
        wait_until_due(ticker, clock.sleep)
    assert starts == [1000.0, 1030.0, 1060.0, 1090.0]


def test_slightly_late_tick_fires_straight_away():
    clock = FakeClock(0.0)
    ticker = Ticker(30, clock)
    clock.t = 31.0  # work took 31 s
    assert ticker.advance() == 0
    assert ticker.seconds_until_due() == 0
    ticker.advance()
    assert ticker.next_due == 60.0  # back on the original grid


def test_overrun_skips_missed_ticks_without_a_burst():
    clock = FakeClock(0.0)
    ticker = Ticker(30, clock)
    clock.t = 95.0  # missed the ticks at 30 and 60
    assert ticker.advance() == 2
    assert ticker.next_due == 90.0  # one catch-up tick now...
    ticker.advance()
    assert ticker.next_due == 120.0  # ...then back on the grid


def test_wait_sleeps_in_short_steps():
    clock = FakeClock(0.0)
    ticker = Ticker(30, clock)
    ticker.advance()
    naps = []

    def sleep(s):
        naps.append(s)
        clock.sleep(s)

    wait_until_due(ticker, sleep)
    assert clock.t == 30.0
    assert max(naps) <= 1.0


def test_wait_ends_early_when_stopped():
    clock = FakeClock(0.0)
    ticker = Ticker(30, clock)
    ticker.advance()
    stop = threading.Event()
    stop.set()
    wait_until_due(ticker, clock.sleep, stop)
    assert clock.t == 0.0
