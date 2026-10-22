"""Fixed-rate timing on a monotonic clock.

Due times are start + n * interval, so the time a tick's work takes never
shifts later ticks (no drift), and a change to the wall clock has no effect.
"""

import time


class Ticker:
    """Says when the next tick is due. The first tick is due immediately."""

    def __init__(self, interval: float, clock=time.monotonic):
        self._interval = interval
        self._clock = clock
        self.next_due = clock()

    def seconds_until_due(self) -> float:
        return max(0.0, self.next_due - self._clock())

    def advance(self) -> int:
        """Move to the next due time. Returns how many ticks were skipped.

        A tick that runs a little late still fires straight away. If the work
        overran by a whole interval or more, the missed ticks are skipped, so
        there is at most one catch-up tick and never a burst.
        """
        self.next_due += self._interval
        behind = self._clock() - self.next_due
        skipped = int(behind // self._interval) if behind > 0 else 0
        self.next_due += skipped * self._interval
        return skipped


def wait_until_due(ticker: Ticker, sleep=time.sleep, stop=None, step: float = 1.0) -> None:
    """Sleep until the ticker is due, in short steps.

    Short steps keep Ctrl+C quick on Windows and let `stop` (a threading.Event)
    end the wait early.
    """
    while not (stop is not None and stop.is_set()):
        remaining = ticker.seconds_until_due()
        if remaining <= 0:
            return
        sleep(min(step, remaining))
