"""Round deadlines on a game's own calendar.

Every deadline is on the game's own clock: a round closes at 23:59 in the game's time zone
on its due date (the `due` of its entry in `Game.schedule`). That is why creating a game
needs a time zone (see `validate_timezone`).
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone as dt_timezone
from zoneinfo import ZoneInfo

CLOSING_TIME = time(23, 59)


def zone(name: str | None) -> ZoneInfo:
    """The named IANA zone, falling back to UTC for a missing or unknown name."""
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def is_valid_timezone(name: str | None) -> bool:
    if not name or not isinstance(name, str) or not name.strip():
        return False
    try:
        ZoneInfo(name.strip())
    except Exception:
        return False
    return True


def validate_timezone(name: str | None) -> str:
    """Returns the cleaned name, or raises ValueError with a message fit to show a person."""
    if not name or not str(name).strip():
        raise ValueError("Choose the time zone the class meets in (for example America/New_York).")
    cleaned = str(name).strip()
    if not is_valid_timezone(cleaned):
        raise ValueError(f"'{cleaned}' is not a real time zone. Choose the time zone the class meets in.")
    return cleaned


def now_utc() -> datetime:
    return datetime.now(dt_timezone.utc)


def today_in(tz: str | None, now: datetime | None = None) -> date:
    """Today's date on the game's own calendar."""
    return (now or now_utc()).astimezone(zone(tz)).date()


def end_of_day(d: date, tz: str | None) -> datetime:
    return datetime.combine(d, CLOSING_TIME, tzinfo=zone(tz))


def start_of_day(d: date, tz: str | None) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=zone(tz))


def schedule_entry(game, rnd: int) -> dict | None:
    for row in game.schedule or []:
        try:
            if int(row.get("round")) == int(rnd):
                return row
        except (TypeError, ValueError):
            continue
    return None


def round_deadline(game, rnd: int) -> datetime | None:
    """The instant round `rnd` closes (23:59 on its due date, game time), else None."""
    entry = schedule_entry(game, rnd)
    if not entry or not entry.get("due"):
        return None
    try:
        return end_of_day(date.fromisoformat(entry["due"]), game.timezone)
    except ValueError:
        return None


def round_opens(game, rnd: int) -> datetime | None:
    entry = schedule_entry(game, rnd)
    if not entry or not entry.get("opens"):
        return None
    try:
        return start_of_day(date.fromisoformat(entry["opens"]), game.timezone)
    except ValueError:
        return None


def current_deadline(game) -> datetime | None:
    return round_deadline(game, game.current_round)


def round_length_days(game, rnd: int) -> int:
    """Days the round runs, from its schedule entry (falls back to the game's pacing)."""
    entry = schedule_entry(game, rnd)
    if entry and entry.get("opens") and entry.get("due"):
        try:
            return (date.fromisoformat(entry["due"]) - date.fromisoformat(entry["opens"])).days + 1
        except ValueError:
            pass
    return max(int(game.round_pacing_days or 7), 1)


def clock_runs(game) -> bool:
    """Whether this game's deadlines are live: deployed to students, not a practice game,
    and not yet complete. Practice games have no deadlines."""
    from .models import GameStatus  # local: avoid a cycle at import time
    return bool(game.student_deployed and not game.is_practice and game.status != GameStatus.COMPLETE)
