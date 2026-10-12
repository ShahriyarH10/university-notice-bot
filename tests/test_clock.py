import re
from datetime import UTC, datetime, timedelta

from notice_bot.app import _now

FORMAT = "%Y-%m-%d %H:%M:%S"


def test_now_is_bangladesh_time_in_sortable_format():
    before = datetime.now(UTC) + timedelta(hours=6)
    stamp = _now()
    after = datetime.now(UTC) + timedelta(hours=6)

    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", stamp)
    assert before.strftime(FORMAT) <= stamp <= after.strftime(FORMAT)
