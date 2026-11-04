"""Per-round schedule: dates for each of a sim's rounds, built from a start date and
the round length, and adjustable when faculty extend a round."""
from __future__ import annotations

from datetime import date, timedelta


def _iso(d: date) -> str:
    return d.isoformat()


def build_schedule(start_date, pacing_days: int, num_rounds: int) -> list[dict]:
    """Round r opens on start + (r-1)*pacing and is due (pacing-1) days later, so
    consecutive rounds sit back-to-back without overlapping. Empty when no start date."""
    if not start_date:
        return []
    pacing = max(int(pacing_days or 7), 1)
    rows = []
    for r in range(1, int(num_rounds) + 1):
        opens = start_date + timedelta(days=(r - 1) * pacing)
        due = opens + timedelta(days=pacing - 1)
        rows.append({"round": r, "opens": _iso(opens), "due": _iso(due)})
    return rows


def extend_round(schedule: list[dict], round_no: int, new_due: date, cascade: bool = True) -> list[dict]:
    """Set round `round_no`'s due date to `new_due`. When cascading (default), every later
    round shifts by the same delta so the spacing after the extended round is preserved."""
    if not schedule:
        raise ValueError("This sim has no schedule — set a start date first.")
    out = [dict(row) for row in schedule]
    idx = next((i for i, row in enumerate(out) if int(row.get("round")) == int(round_no)), None)
    if idx is None:
        raise ValueError(f"Round {round_no} is not in the schedule.")

    old_due = date.fromisoformat(out[idx]["due"])
    if new_due < date.fromisoformat(out[idx]["opens"]):
        raise ValueError("A round's due date can't be before it opens.")
    delta = (new_due - old_due).days
    out[idx]["due"] = _iso(new_due)
    if delta:
        out[idx]["extended"] = True      # a faculty member moved this deadline (reporting reads it)

    if cascade and delta:
        for row in out[idx + 1:]:
            row["opens"] = _iso(date.fromisoformat(row["opens"]) + timedelta(days=delta))
            row["due"] = _iso(date.fromisoformat(row["due"]) + timedelta(days=delta))
            row["extended"] = True
    return out


def rebuild_from(schedule: list[dict], current_round: int, today: date, pacing_days: int,
                 num_rounds: int) -> list[dict]:
    """Re-time the schedule so the current round opens `today` and later rounds follow
    back-to-back at the usual length. Rounds before the current one keep their dates.

    Deploying a game to students starts play; without this, the clock would still be the one
    set when the game was *created*, so a game deployed days later could open with its
    deadline already past."""
    pacing = max(int(pacing_days or 7), 1)
    cur = max(int(current_round or 1), 1)
    kept = [dict(row) for row in (schedule or []) if int(row.get("round", 0)) < cur]
    for r in range(cur, int(num_rounds) + 1):
        opens = today + timedelta(days=(r - cur) * pacing)
        kept.append({"round": r, "opens": _iso(opens), "due": _iso(opens + timedelta(days=pacing - 1))})
    return kept
