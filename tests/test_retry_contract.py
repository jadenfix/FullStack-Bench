from email.utils import formatdate
import pytest
from fsbench.retry_contract import logical_request


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


def run(responses):
    clock = Clock()
    timeouts = []
    source = iter(responses)

    def once(timeout):
        timeouts.append(timeout)
        return next(source)

    result = logical_request(
        once, clock=clock, sleep=clock.sleep, wall_clock=lambda: 1000 + clock.now
    )
    return result, clock, timeouts


def test_recovered_request_includes_the_full_uncapped_retry_delay():
    result, clock, timeouts = run(
        [(503, {}, "5", None), (200, {"ok": True}, None, None)]
    )
    assert result["status"] == 200 and result["attempts"] == 2 and result["ms"] == 5000
    assert clock.sleeps == [5] and timeouts == [10, 10]


@pytest.mark.parametrize(
    "status,header,error",
    [
        (503, None, None),
        (500, "1", None),
        (0, None, "reset"),
        (0, None, "timeout"),
        (503, "NaN", None),
        (503, "-1", None),
    ],
)
def test_unsafe_or_unspecified_retry_is_a_single_failure(status, header, error):
    result, clock, _ = run([(status, {}, header, error)])
    assert result["status"] == status and result["attempts"] == 1 and clock.sleeps == []


def test_refused_connection_retries_and_recovers():
    result, clock, _ = run([(0, {}, None, "refused"), (200, {}, None, None)])
    assert result["status"] == 200 and result["attempts"] == 2 and clock.sleeps == [1]


def test_no_new_attempt_starts_beyond_the_twenty_second_limit():
    result, clock, _ = run([(503, {}, "30", None)])
    assert result["status"] == 503 and result["attempts"] == 1
    assert clock.sleeps == [20] and result["ms"] == 20000


def test_http_date_retry_after_is_honored():
    result, clock, _ = run(
        [(503, {}, formatdate(1005, usegmt=True), None), (200, {}, None, None)]
    )
    assert result["status"] == 200 and clock.sleeps == [5]


def test_each_socket_timeout_is_bounded_by_remaining_request_time():
    clock = Clock()
    timeouts = []

    def once(timeout):
        timeouts.append(timeout)
        clock.now += 2
        return (200, {}, None, None) if len(timeouts) == 3 else (503, {}, "6", None)

    result = logical_request(once, clock=clock, sleep=clock.sleep)
    assert (
        timeouts == [10, 10, 4] and result["attempts"] == 3 and result["status"] == 200
    )
