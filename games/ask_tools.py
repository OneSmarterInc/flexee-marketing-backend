"""Ask Claude's lookups: read-only, and limited to the one game on screen.

They run our own code against data loaded for that game, so nothing Claude asks for can change
anything or reach another game. Team names are given; people's names never are. Every figure is
rounded here, once, and handed over as written: Claude quotes it exactly and never works out new
ones, and the figure check drops any sentence whose figure the lookups did not return.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import models
from .deadlines import round_deadline, zone

MAX_ROUNDS = 5


def _pct(v):
    return None if v is None else v * 100.0


# measure key -> (label, extractor(rec, team) -> number | None, digits)
def _score(key):
    return lambda rec, t: ((rec.get("scorecard", {}).get("firms", {}).get(t) or {}).get("normalized") or {}).get(key)


MEASURES = {
    "overall": ("overall score", lambda rec, t: (rec.get("scorecard", {}).get("firms", {}).get(t) or {}).get("overall"), 1),
    "brand_health": ("brand health score", _score("brand_health"), 1),
    "customer_value": ("customer value score", _score("customer_value"), 1),
    "market_position": ("market position score", _score("market_position"), 1),
    "financial": ("financial score", _score("financial"), 1),
    "market_share": ("market share, % of the market", lambda rec, t: _pct((rec.get("firms", {}).get(t) or {}).get("total_market_share")), 1),
    "growth_share": ("share of the growth segments, %", lambda rec, t: _pct((rec.get("firms", {}).get(t) or {}).get("growth_segment_share")), 1),
    "revenue": ("revenue", lambda rec, t: (rec.get("firms", {}).get(t) or {}).get("revenue"), 2),
    "contribution": ("contribution", lambda rec, t: (rec.get("firms", {}).get(t) or {}).get("contribution"), 2),
    "volume": ("units sold", lambda rec, t: (rec.get("firms", {}).get(t) or {}).get("total_volume"), 2),
    "brand_equity": ("brand equity", lambda rec, t: (rec.get("firms", {}).get(t) or {}).get("eq"), 1),
    "trust": ("brand trust", lambda rec, t: (rec.get("attribute_state", {}).get(t) or {}).get("TRUST"), 1),
}
MEASURE_KEYS = list(MEASURES) + ["rank"]

SEGMENT_NAMES = ("Legacy Core", "Fitness & Performance", "Clean-Wellness", "Gen-Z Emergent", "Mainstream")
MESSAGE_NAMES = {"NEUTRAL": "neutral", "PERFORMANCE": "performance", "HERITAGE": "heritage",
                 "CLEAN_ENERGY": "clean-energy", "COMMUNITY": "community"}


def _num(v, digits):
    if v is None:
        return None
    r = round(float(v), digits)
    return int(r) if digits == 0 else r


def _when(dt: datetime | None, tz: ZoneInfo) -> str | None:
    """A moment written out in words, in the game's time zone, so no figure has to be worked out."""
    if dt is None:
        return None
    local = dt.astimezone(tz)
    return f"{local:%a, %b} {local.day} {local.year} at {local:%H:%M} ({tz.key})"


def _span(delta_seconds: float) -> str:
    s = max(0, int(delta_seconds))
    days, rest = divmod(s, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} day{'s' if days != 1 else ''} {hours} hour{'s' if hours != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''} {minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


class Lookup(dict):
    """{"name", "summary"}: one line for "Claude looked up: …"."""


class AskTools:
    """The lookups, over one game's data loaded once."""

    def __init__(self, game: models.Game, now: datetime | None = None):
        self.game = game
        self.now = now or datetime.now(timezone.utc)
        self.tz = zone(game.timezone)
        self.teams = list(game.teams.all().order_by("name"))
        self.team_names = [t.name for t in self.teams]
        self.results = {r.round: r.raw for r in game.results.all()}
        self.views: list[dict] = []          # tables to show beside the answer

    # ---- helpers
    def _closed(self) -> list[int]:
        return sorted(self.results)

    def _pick_teams(self, names):
        if not names:
            return self.team_names
        wanted = {str(n).strip().lower() for n in names}
        return [t for t in self.team_names if t.lower() in wanted]

    def _pick_rounds(self, rounds):
        closed = self._closed()
        if not rounds:
            return closed[-1:]
        picked = sorted({int(r) for r in rounds if int(r) in self.results})
        return picked[:MAX_ROUNDS]

    def _ranks(self, rec) -> dict:
        overall = {t: MEASURES["overall"][1](rec, t) for t in self.team_names}
        order = sorted((t for t in overall if overall[t] is not None), key=lambda t: -overall[t])
        return {t: i + 1 for i, t in enumerate(order)}

    def _row(self, rec, team, keys):
        row = {}
        ranks = self._ranks(rec) if "rank" in keys else {}
        for k in keys:
            if k == "rank":
                row["rank among teams (1 is best)"] = ranks.get(team)
            elif k in MEASURES:
                label, get, digits = MEASURES[k]
                row[label] = _num(get(rec, team), digits)
        return row

    # ---- lookups
    def status(self) -> dict:
        g = self.game
        phase = ("finished" if g.status == models.GameStatus.COMPLETE
                 else "running" if g.student_deployed
                 else "not yet opened to students" if g.deployed else "being set up")
        rnd = g.current_round
        round_now = None
        if phase != "finished":
            deadline = round_deadline(g, rnd) if not g.is_practice else None
            from .services import GameService
            round_now = {
                "round": rnd, "of": g.num_rounds,
                "listening_period": rnd < g.takeover_round,
                "closes": _when(deadline, self.tz),
                "time_left": _span((deadline - self.now).total_seconds()) if deadline and deadline > self.now else None,
                "levers_open": GameService(g).open_levers(rnd),
            }
        submitted = set(g.submissions.filter(round=rnd).values_list("team_id", flat=True))
        teams = []
        for t in self.teams:
            row = {"team": t.name, "students": t.users.filter(role="student").count()}
            if t.reference_path:
                row["reference_firm"] = True
            if round_now and round_now["levers_open"]:
                row["submitted_this_round"] = t.id in submitted
            teams.append(row)
        return {
            "game": g.name, "phase": phase, "round_now": round_now,
            "first_decision_round": g.takeover_round, "rounds_closed": len(self._closed()),
            "time_zone": g.timezone, "practice_game": g.is_practice,
            "ai": "on" if g.ai_enabled else "off", "teams": teams,
        }

    def close_history(self) -> dict:
        rows = models.RoundCloseLog.objects.filter(game_id_ref=self.game.pk).order_by("round", "completed_at")
        closes = [{
            "round": r.round,
            "closed": _when(r.completed_at, self.tz),
            "how": "at the deadline" if r.source == "deadline" else "by a faculty member pressing Advance",
            "outcome": r.status,
            "teams": r.firms, "teams_submitted": r.firms_submitted,
            "teams_run_on_cohort_average": r.firms_carried_forward,
            "took_seconds": round(r.seconds, 1),
        } for r in rows]
        return {"closes": closes}

    def results_lookup(self, args: dict) -> dict:
        keys = [k for k in (args.get("measures") or list(MEASURES) + ["rank"]) if k in MEASURE_KEYS]
        teams, rounds = self._pick_teams(args.get("teams")), self._pick_rounds(args.get("rounds"))
        if not self._closed():
            return {"results": [], "note": "No round has closed yet, so there are no stored results."}
        if not rounds:
            return {"results": [], "note": f"Those rounds are not closed. Closed rounds: {self._closed()}."}
        out = []
        for t in teams:
            out.append({"team": t, "rounds": [{"round": r, **self._row(self.results[r], t, keys)} for r in rounds]})
        return {"results": out}

    def table(self, args: dict) -> tuple[dict, dict | None]:
        result = self.results_lookup(args)
        rows = result.get("results") or []
        if not rows:
            return result, None
        columns = [k for k in rows[0]["rounds"][0] if k != "round"]
        flat = [{"team": r["team"], **rd} for r in rows for rd in r["rounds"]]
        view = {"columns": ["team", "round", *columns], "rows": [[row.get(c) for c in ["team", "round", *columns]] for row in flat]}
        return {**result, "shown_as_table": True}, view

    def team_detail(self, args: dict) -> dict:
        picked = self._pick_teams([args.get("team")]) if args.get("team") else []
        if not picked:
            return {"error": f"There is no team called {args.get('team')!r}. Teams: {self.team_names}."}
        team = picked[0]
        closed = self._closed()
        if not closed:
            return {"team": team, "note": "No round has closed yet."}
        last = closed[-1]
        return {"team": team, "as_of_round": last, **self._row(self.results[last], team, list(MEASURES) + ["rank"])}

    def decisions(self, args: dict) -> dict:
        picked = self._pick_teams([args.get("team")]) if args.get("team") else []
        if not picked:
            return {"error": f"There is no team called {args.get('team')!r}. Teams: {self.team_names}."}
        team = next(t for t in self.teams if t.name == picked[0])
        try:
            rnd = int(args.get("round"))
        except (TypeError, ValueError):
            return {"error": "Give a round number."}
        sub = self.game.submissions.filter(team=team, round=rnd).first()
        out = {"team": team.name, "round": rnd}
        if team.reference_path:
            out["reference_firm"] = True
        if sub is None:
            closed = rnd in self.results
            out["submitted"] = False
            out["note"] = ("No decision was submitted; the team played this round on the cohort average."
                           if closed else "No decision submitted yet.")
            return out
        lv = sub.submitted_levers or {}
        out["submitted"] = True
        d = {}
        if "brand" in lv:
            d["budget split"] = f"brand {_fmt(lv['brand'])}, performance {_fmt(lv['perf'])}, research {_fmt(lv['research'])}"
        if "emphasis" in lv:
            d["segment emphasis"] = ", ".join(f"{n} {round(float(w) * 100)}%" for n, w in zip(SEGMENT_NAMES, lv["emphasis"]))
        if "positioning" in lv:
            d["positioning"] = f"{_fmt(lv['positioning'])} (0 is heritage, 100 is wellness)"
        if "promotion" in lv:
            d["promotion"] = _fmt(lv["promotion"])
        if "price" in lv:
            d["price"] = _fmt(lv["price"])
        if isinstance(lv.get("channel"), dict):
            d["channel mix"] = ", ".join(f"{c.lower()} {round(float(w) * 100)}%" for c, w in lv["channel"].items())
        if lv.get("message"):
            d["message"] = MESSAGE_NAMES.get(lv["message"], lv["message"])
        if isinstance(lv.get("line_extension"), dict):
            le = lv["line_extension"]
            d["functional line"] = (f"launch, positioned at {_fmt(le.get('positioning'))}" if le.get("launch") else "not launched")
        out["decisions"] = d
        return out

    def forecast_accuracy(self, args: dict) -> dict:
        teams = self._pick_teams(args.get("teams"))
        wanted = {int(r) for r in (args.get("rounds") or []) if str(r).lstrip("-").isdigit()}
        rows = []
        for f in self.game.forecasts.select_related("team").order_by("team__name", "round"):
            if f.team.name not in teams or (wanted and f.round not in wanted):
                continue
            rows.append({
                "team": f.team.name, "round": f.round,
                "forecasting": "market share" if f.metric == "own_share" else "growth-segment share",
                "predicted_pct": _num(f.predicted, 1),
                "actual_pct": _num(f.actual, 1) if f.actual is not None else None,
                "accuracy_pct": _num(f.accuracy * 100, 1) if f.accuracy is not None else None,
            })
        return {"forecasts": rows} if rows else {"forecasts": [], "note": "No forecasts yet."}

    def engagement(self) -> dict:
        from .services import GameService
        counts = GameService(self.game).engagement_counts()
        return {"engagement": [{"team": t, "focus_groups_and_advisor_consults": counts.get(t, 0)} for t in self.team_names]}

    # ---- the tool interface Claude sees
    def definitions(self) -> list[dict]:
        measure_help = ("Which measures: " + ", ".join(f"{k} ({v[0]})" for k, v in MEASURES.items())
                        + ", rank. Leave out for all of them.")
        pick = {
            "measures": {"type": "array", "items": {"type": "string", "enum": MEASURE_KEYS}, "description": measure_help},
            "teams": {"type": "array", "items": {"type": "string"}, "description": 'Team names, such as "Team A". Leave out for every team.'},
            "rounds": {"type": "array", "items": {"type": "integer"}, "description": f"Closed rounds, at most {MAX_ROUNDS}. Leave out for the latest closed round."},
        }
        return [
            {"name": "get_game_status", "description": (
                "Where this game stands now: its phase, the round now open and of how many, when it closes and the time left "
                "(in the game's time zone), which levers are open, each team with its student count and whether it has submitted, "
                "and whether AI is on."), "input_schema": {"type": "object", "properties": {}}},
            {"name": "get_close_history", "description": (
                "Every round closed so far, as recorded: when (game time zone), whether at the deadline or by a faculty member, "
                "how many teams submitted, how many ran on the cohort average, and how long the close took."),
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "get_results", "description": (
                "Stored results at the close of chosen rounds: chosen measures for chosen teams (every team unless named). "
                "At most 5 rounds; the latest closed round if none is named. Use it to read results; use show_table when the "
                "faculty member should see the figures too."),
             "input_schema": {"type": "object", "properties": {k: pick[k] for k in ("measures", "teams", "rounds")}}},
            {"name": "show_table", "description": (
                "The same stored results as get_results, also shown to the faculty member as a table beside your answer. Call it at "
                "most once per answer, when figures matter. Keep it small: one measure over a few rounds, or a few measures at one round."),
             "input_schema": {"type": "object", "properties": {k: pick[k] for k in ("measures", "teams", "rounds")}, "required": ["measures"]}},
            {"name": "get_team_detail", "description": "One team as of the latest closed round: every measure and its rank.",
             "input_schema": {"type": "object", "properties": {"team": {"type": "string"}}, "required": ["team"]}},
            {"name": "get_decisions", "description": (
                "What one team decided in one round, in plain words: budget split, segment emphasis, positioning, promotion, price, "
                "channel mix, message and functional line (whichever were open), or that it did not submit."),
             "input_schema": {"type": "object", "properties": {"team": {"type": "string"}, "round": {"type": "integer"}}, "required": ["team", "round"]}},
            {"name": "get_forecast_accuracy", "description": (
                "Forecast accuracy by team and round: what the team predicted, what happened, and accuracy, in percent."),
             "input_schema": {"type": "object", "properties": {"teams": pick["teams"], "rounds": pick["rounds"]}}},
            {"name": "get_engagement", "description": "How many focus groups and advisor consults each team has used.",
             "input_schema": {"type": "object", "properties": {}}},
        ]

    def run(self, name: str, args: dict) -> dict:
        args = args if isinstance(args, dict) else {}
        try:
            if name == "get_game_status":
                return self.status()
            if name == "get_close_history":
                return self.close_history()
            if name == "get_results":
                return self.results_lookup(args)
            if name == "show_table":
                result, view = self.table(args)
                if view and not self.views:
                    self.views.append(view)
                return result
            if name == "get_team_detail":
                return self.team_detail(args)
            if name == "get_decisions":
                return self.decisions(args)
            if name == "get_forecast_accuracy":
                return self.forecast_accuracy(args)
            if name == "get_engagement":
                return self.engagement()
        except (TypeError, ValueError, KeyError) as exc:
            return {"error": f"That lookup could not run: {exc}"}
        return {"error": f"There is no lookup called {name}."}

    def summary(self, name: str, result: dict) -> str:
        if result.get("error"):
            return str(result["error"])
        if name == "get_game_status":
            r = result.get("round_now")
            return f"where the game stands: round {r['round']} open" if r else f"where the game stands: {result['phase']}"
        if name == "get_close_history":
            n = len(result["closes"])
            return f"the close history, {n} close{'s' if n != 1 else ''}" if n else "the close history: no rounds closed yet"
        if name in ("get_results", "show_table"):
            rows = result.get("results") or []
            if not rows:
                return "stored results: none yet"
            rs = [x["round"] for x in rows[0]["rounds"]]
            span = f"rounds {rs[0]} to {rs[-1]}" if len(rs) > 1 else f"round {rs[0]}"
            return f"{len(rows)} team{'s' if len(rows) != 1 else ''}' stored results for {span}" + (", shown as a table" if name == "show_table" else "")
        if name == "get_team_detail":
            return f"{result.get('team')}'s latest results"
        if name == "get_decisions":
            return f"{result.get('team')}'s round {result.get('round')} decisions"
        if name == "get_forecast_accuracy":
            return "forecast accuracy by team and round"
        if name == "get_engagement":
            return "focus group and advisor use by team"
        return "nothing"


def _fmt(v):
    if v is None:
        return "not set"
    f = float(v)
    return str(int(f)) if f == int(f) else f"{f:g}"
