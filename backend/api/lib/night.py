from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# A "night" of karaoke runs until 6am local time, so a request at 1am on Saturday
# belongs to Friday's night.
ROLLOVER_HOUR = 6


def _zone(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # The Lambda base image has no system tz database; dateutil (shipped with
        # botocore) bundles its own.
        from dateutil import tz

        return tz.gettz(name)


def night_date(now=None, time_zone='America/Los_Angeles'):
    now = now or datetime.now(timezone.utc)
    shifted = now - timedelta(hours=ROLLOVER_HOUR)
    return shifted.astimezone(_zone(time_zone)).strftime('%Y-%m-%d')
