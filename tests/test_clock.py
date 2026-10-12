import re
from datetime import datetime

from notice_bot.app import _now


def test_now_is_iso_8601_in_bangladesh_time():
    stamp = _now()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+06:00", stamp)
    assert datetime.fromisoformat(stamp).utcoffset().total_seconds() == 6 * 3600
