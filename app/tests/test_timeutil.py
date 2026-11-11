import datetime

from app.timeutil import to_local, to_utc


def test_round_trip_and_none():
    x = datetime.datetime(2026, 11, 10, 23, 30)
    assert to_utc(to_local(x)) == x
    assert to_local(None) is None and to_utc(None) is None
