"""DjangoStore — the persistence version of flexee_service.InMemoryStore.

Drop-in replacement with the IDENTICAL surface FlexeeGame touches, so wiring the proven
spine to the database is exactly:

    from flexee_service import FlexeeGame
    game = FlexeeGame(team_names, model_client=mc, store=DjangoStore(game_row))

FlexeeGame accesses the store through two methods and six dict/list-like attributes
(verified against flexee_service.py):

    save_submission(rnd, team, decision)      round_results[rnd]        (get/set, iterate keys)
    get_submissions_through(t)                effective_decisions[rnd]  (baseline-filled: get/set/in)
                                              relationships[team]       (write-through mapping)
                                              focus_groups.append(...) / iterate
                                              advisor_consults.append(...) / iterate
                                              defenses[team] = combined

`effective_decisions` is the round -> {team -> baseline-filled decision} capture the
updated spine populates in close_round (from a (history, built) tuple or a record-embedded
key); board_defense reads it, backed here by RoundResult.effective_decisions.

Each attribute is a thin write-through proxy over the Django models, so every mutation the
spine makes is persisted immediately and re-reads come straight from the DB. Nothing here
carries simulation state — the runner stays the single source of truth.
"""

from __future__ import annotations

from typing import Iterator

from games import models


# --- record <-> RoundResult mapping ------------------------------------------------
_RECORD_COLUMNS = (
    "firms", "scorecard", "equity", "attribute_state", "deal_conditioning",
    "promotion_depth", "damage", "segment_sizes", "open_levers", "effective_decisions",
)


def _columns_from_record(record: dict) -> dict:
    cols = {"raw": record}
    for key in _RECORD_COLUMNS:
        if key in record:
            cols[key] = record[key]
    return cols


def _record_from_row(row: "models.RoundResult") -> dict:
    # `raw` is the faithful round-trip; fall back to the denormalized columns for rows
    # written before `raw` existed.
    if row.raw:
        return row.raw
    rec = {"round": row.round}
    for key in _RECORD_COLUMNS:
        rec[key] = getattr(row, key)
    return rec


# --- round_results: dict[int -> record] -------------------------------------------
class _RoundResults:
    def __init__(self, game):
        self._game = game

    def __setitem__(self, rnd: int, record: dict) -> None:
        models.RoundResult.objects.update_or_create(
            game=self._game, round=rnd, defaults=_columns_from_record(record))

    def __getitem__(self, rnd: int) -> dict:
        row = models.RoundResult.objects.get(game=self._game, round=rnd)
        return _record_from_row(row)

    def __contains__(self, rnd: int) -> bool:
        return models.RoundResult.objects.filter(game=self._game, round=rnd).exists()

    def __iter__(self) -> Iterator[int]:
        return iter(list(models.RoundResult.objects.filter(game=self._game)
                         .order_by("round").values_list("round", flat=True)))

    def keys(self):
        return list(self.__iter__())

    def __len__(self) -> int:
        return models.RoundResult.objects.filter(game=self._game).count()

    def get(self, rnd, default=None):
        try:
            return self[rnd]
        except models.RoundResult.DoesNotExist:
            return default


# --- effective_decisions: dict[round -> {team -> baseline-filled decision}] --------
# Backed by RoundResult.effective_decisions. The spine sets round_results[rnd]=record
# first (which creates the row), then effective_decisions[rnd]=built[rnd-1].
class _EffectiveDecisions:
    def __init__(self, game):
        self._game = game

    def __setitem__(self, rnd: int, mapping: dict) -> None:
        models.RoundResult.objects.update_or_create(
            game=self._game, round=rnd,
            defaults={"effective_decisions": mapping})

    def __getitem__(self, rnd: int) -> dict:
        return (models.RoundResult.objects
                .get(game=self._game, round=rnd).effective_decisions)

    def __contains__(self, rnd: int) -> bool:
        # present == a row exists AND it actually carries decisions (empty == not captured,
        # so _decisions_by_round raises its clear error rather than yielding {} to build_defense).
        row = models.RoundResult.objects.filter(game=self._game, round=rnd).first()
        return bool(row and row.effective_decisions)

    def get(self, rnd, default=None):
        row = models.RoundResult.objects.filter(game=self._game, round=rnd).first()
        return row.effective_decisions if (row and row.effective_decisions) else default

    def __iter__(self) -> Iterator[int]:
        rows = models.RoundResult.objects.filter(game=self._game).order_by("round")
        return iter([r.round for r in rows if r.effective_decisions])


# --- relationships: dict[team -> {persona_id -> float}] (write-through) ------------
class _TeamRelationships:
    """A live, write-through mapping of persona_id -> relationship for one team."""

    def __init__(self, game, team):
        self._game = game
        self._team = team

    def __getitem__(self, persona_id: str) -> float:
        return (models.PersonaRelationship.objects
                .get(game=self._game, team=self._team, persona_id=persona_id).relationship)

    def __setitem__(self, persona_id: str, value: float) -> None:
        models.PersonaRelationship.objects.update_or_create(
            game=self._game, team=self._team, persona_id=persona_id,
            defaults={"relationship": float(value)})

    def __contains__(self, persona_id: str) -> bool:
        return (models.PersonaRelationship.objects
                .filter(game=self._game, team=self._team, persona_id=persona_id).exists())

    def __iter__(self) -> Iterator[str]:
        # materialized so FlexeeGame's `for pid in rels: rels[pid] = ...` (write during
        # iteration) is safe.
        return iter(list(models.PersonaRelationship.objects
                         .filter(game=self._game, team=self._team)
                         .values_list("persona_id", flat=True)))

    def keys(self):
        return list(self.__iter__())

    def items(self):
        return [(pr.persona_id, pr.relationship) for pr in
                models.PersonaRelationship.objects.filter(game=self._game, team=self._team)]

    def get(self, persona_id, default=None):
        try:
            return self[persona_id]
        except models.PersonaRelationship.DoesNotExist:
            return default


class _Relationships:
    def __init__(self, game):
        self._game = game
        self._teams = {t.name: t for t in game.teams.all()}

    def _team(self, name):
        team = self._teams.get(name)
        if team is None:  # tolerate teams created after the store
            team = self._game.teams.get(name=name)
            self._teams[name] = team
        return team

    def __getitem__(self, team_name: str) -> _TeamRelationships:
        return _TeamRelationships(self._game, self._team(team_name))

    def setdefault(self, team_name: str, default: dict) -> _TeamRelationships:
        team = self._team(team_name)
        existing = set(models.PersonaRelationship.objects
                       .filter(game=self._game, team=team)
                       .values_list("persona_id", flat=True))
        to_create = [
            models.PersonaRelationship(game=self._game, team=team,
                                       persona_id=pid, relationship=float(val))
            for pid, val in (default or {}).items() if pid not in existing
        ]
        if to_create:
            models.PersonaRelationship.objects.bulk_create(to_create)
        return _TeamRelationships(self._game, team)

    def __contains__(self, team_name: str) -> bool:
        team = self._teams.get(team_name)
        if team is None:
            return False
        return models.PersonaRelationship.objects.filter(
            game=self._game, team=team).exists()


# --- focus_groups / advisor_consults: append-and-iterate lists --------------------
class _FocusGroups:
    def __init__(self, game):
        self._game = game

    def append(self, record: dict) -> None:
        team = self._game.teams.get(name=record["team"])
        models.FocusGroupSession.objects.create(
            game=self._game, round=record.get("round", 0), team=team,
            persona_ids=record.get("persona_ids", []),
            prompt=record.get("prompt") or "",
            responses=record.get("responses", []))

    def __iter__(self) -> Iterator[dict]:
        for row in self._game.focus_groups.select_related("team"):
            yield {"round": row.round, "team": row.team.name,
                   "persona_ids": row.persona_ids, "prompt": row.prompt,
                   "responses": row.responses}

    def __len__(self) -> int:
        return self._game.focus_groups.count()


class _AdvisorConsults:
    def __init__(self, game):
        self._game = game

    def append(self, record: dict) -> None:
        team = self._game.teams.get(name=record["team"])
        models.AdvisorConsultation.objects.create(
            game=self._game, round=record.get("round", 0), team=team,
            decision_under_review=record.get("decision_under_review", {}),
            stances=record.get("stances", []),
            usage_record=record.get("usage", record.get("usage_record", {})))

    def __iter__(self) -> Iterator[dict]:
        for row in self._game.advisor_consults.select_related("team"):
            yield {"round": row.round, "team": row.team.name,
                   "decision_under_review": row.decision_under_review,
                   "stances": row.stances, "usage": row.usage_record}

    def __len__(self) -> int:
        return self._game.advisor_consults.count()


# --- defenses: dict[team -> combined] ---------------------------------------------
class _Defenses:
    def __init__(self, game):
        self._game = game

    def __setitem__(self, team_name: str, combined: dict) -> None:
        team = self._game.teams.get(name=team_name)
        models.DefenseSubmission.objects.update_or_create(
            game=self._game, team=team,
            defaults={
                "combined": combined,
                "deterministic_grade": combined.get("deterministic", {}) or {},
                "prose_grade": combined.get("prose") or {},
                "defense_text": combined.get("defense_text", ""),
            })

    def __getitem__(self, team_name: str) -> dict:
        team = self._game.teams.get(name=team_name)
        return models.DefenseSubmission.objects.get(game=self._game, team=team).combined

    def __contains__(self, team_name: str) -> bool:
        return models.DefenseSubmission.objects.filter(
            game=self._game, team__name=team_name).exists()


# --- the store --------------------------------------------------------------------
class DjangoStore:
    """Persistence version of InMemoryStore. Same surface; writes go to the database."""

    def __init__(self, game):
        self.game = game
        self.round_results = _RoundResults(game)
        self.effective_decisions = _EffectiveDecisions(game)
        self.relationships = _Relationships(game)
        self.focus_groups = _FocusGroups(game)
        self.advisor_consults = _AdvisorConsults(game)
        self.defenses = _Defenses(game)

    def _team(self, name):
        return self.game.teams.get(name=name)

    def save_submission(self, rnd: int, team: str, decision: dict) -> None:
        models.RoundSubmission.objects.update_or_create(
            game=self.game, round=rnd, team=self._team(team),
            defaults={"submitted_levers": dict(decision)})

    def get_submissions_through(self, t: int) -> list[dict]:
        """0-indexed list; element i = round (i+1) as {team_name: decision} (matches InMemoryStore)."""
        rows: list[dict] = [{} for _ in range(t)]
        for sub in self.game.submissions.filter(round__lte=t).select_related("team"):
            if 1 <= sub.round <= t:
                rows[sub.round - 1][sub.team.name] = sub.submitted_levers or {}
        return rows
