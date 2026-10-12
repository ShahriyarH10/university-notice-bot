import re
from datetime import UTC, datetime, timedelta

from notice_bot.app import _now


def test_now_is_time_of_day_in_bangladesh_time():
    before = datetime.now(UTC) + timedelta(hours=6)
    stamp = _now()
    after = datetime.now(UTC) + timedelta(hours=6)

    assert re.fullmatch(r"\d\d:\d\d:\d\d", stamp)
    assert before.strftime("%H:%M:%S") <= stamp <= after.strftime("%H:%M:%S") or stamp[:2] in {
        before.strftime("%H"),
        after.strftime("%H"),
    }
