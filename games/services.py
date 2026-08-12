"""Service layer — the DB-backed version of the reference flexee_service.py.

Ties the persisted submissions to the pure engine. The runner is PURE: every round
close re-derives the whole history from the submissions of record (rounds 1..t), so no
simulation state is ever carried by hand. The AI never moves a number — it renders the
deterministic state that the engine computed.
"""

from __future__ import annotations

import logging
from copy import deepcopy

from django.db import transaction
from rest_framework.exceptions import ValidationError

from flexee_engine import api
from . import models
from .validators import validate_decision

log = logging.getLogger("flexee.service")

SEGS = api.SEGS


SCORECARD_DIMS = ("brand_health", "customer_value", "market_position", "financial")


def derive_crisis_inputs(pre_crisis_record, teams, severity=1.0, default_response=0.3):
    """Derive per-team crisis_inputs from the pre-crisis (round crisis-1) state.

    Heritage lean (low positioning) -> high exposure; carried TRUST -> preempt
    (preparedness). Pure app-layer: the R9 hit lands on the heritage/high-caffeine team and
    the wellness-builder rides it out, with no engine or golden change. Damage formula is
    damage_base * exposure * (1 - eq/100) * (1 - preempt) * (1 - response).
    """
    out = {}
    eff = pre_crisis_record.get("effective_decisions", {})
    attrs = pre_crisis_record.get("attribute_state", {})
    for t in teams:
        pos = float(eff.get(t, {}).get("positioning", 35.0))
        trust = float(attrs.get(t, {}).get("TRUST", 50.0))
        exposure = max(0.0, min(1.0, severity * (1.0 - pos / 100.0)))
        preempt = max(0.0, min(1.0, trust / 100.0))
        out[t] = {"exposure": exposure, "preempt": preempt, "response": default_response}
    return out


def reweight_overall(normalized, weights):
    """overall = Σ weight_i × dimension_i. With default equal weights this reproduces the
    engine's overall exactly (engine default is 0.25 each)."""
    return sum(float(normalized.get(d, 0.0)) * float(weights.get(d, 0.25))
               for d in SCORECARD_DIMS)


def apply_scorecard_weights(report, weights):
    """Recompute each team's overall, the anonymized cohort best/avg/worst, and rank from
    faculty perspective-weights, in the app layer (no engine change). `report` is the
    build_report output (list of {round, teams})."""
    for rnd in report:
        teams = rnd.get("teams", {})
        for t, tr in teams.items():
            tr["own"]["overall"] = reweight_overall(tr["own"]["normalized"], weights)
        overalls = {t: teams[t]["own"]["overall"] for t in teams}
        if overalls:
            cohort = {"best": max(overalls.values()),
                      "average": sum(overalls.values()) / len(overalls),
                      "worst": min(overalls.values())}
            for t in teams:
                teams[t]["cohort_overall"] = cohort
                teams[t]["rank"] = 1 + sum(1 for u in overalls if overalls[u] > overalls[t])
    return report


def saturating_engagement(raw_counts, per_team_cap=None, half_saturation=6.0):
    """Diminishing-returns transform for engagement (decision 3), pure arithmetic.

    raw_counts: {team: int} = focus groups + advisor consults over the game.
    Optional hard cap first, then hyperbolic saturation so spamming stops paying.
    build_defense min-max-normalizes this across the cohort, so only the SHAPE matters
    (the absolute scale washes out) — feed the returned dict in place of raw counts.
    """
    out = {}
    for team, n in raw_counts.items():
        n = float(n)
        if per_team_cap is not None:
            n = min(n, per_team_cap)
        out[team] = n / (n + half_saturation)
    return out


class GameService:
    def __init__(self, game: models.Game):
        self.game = game
        self.config = game.config_snapshot or api.default_config()

    # ---- setup helpers ----
    def team_names(self) -> list[str]:
        return list(self.game.teams.order_by("name").values_list("name", flat=True))

    def team_by_name(self, name: str) -> models.Team:
        return self.game.teams.get(name=name)

    def open_levers(self, rnd: int) -> list[str]:
        return api.open_levers_for_round(rnd, self.config)

    # ---- round lock / reopen (LOCKED decision) ----
    def is_locked(self, rnd: int) -> bool:
        return rnd in (self.game.locked_rounds or [])

    def lock_round(self, rnd: int) -> None:
        locked = set(self.game.locked_rounds or [])
        locked.add(rnd)
        self.game.locked_rounds = sorted(locked)
        self.game.save(update_fields=["locked_rounds"])

    def reopen_round(self, rnd: int) -> None:
        locked = [r for r in (self.game.locked_rounds or []) if r != rnd]
        self.game.locked_rounds = locked
        self.game.save(update_fields=["locked_rounds"])

    # ---- submissions ----
    def submit(self, rnd: int, team: models.Team, levers: dict, user=None) -> dict:
        if self.is_locked(rnd):
            raise ValidationError(
                {"round": f"round {rnd} is closed; ask faculty to reopen it"})
        cleaned = validate_decision(levers, self.open_levers(rnd), self.config)
        obj, _ = models.RoundSubmission.objects.update_or_create(
            game=self.game, round=rnd, team=team,
            defaults={"submitted_levers": cleaned, "submitted_by": user},
        )
        return obj.submitted_levers

    def _num_rounds(self) -> int:
        return int(self.config.get("num_rounds", self.game.num_rounds))

    def _submissions_through(self, t: int) -> list[dict]:
        """0-indexed list of length num_rounds (the shipping runner requires the full length).

        Rounds 1..t carry the stored submissions; future rounds are empty (the runner fills
        them from the faculty baseline). The runner is sequential/pure, so round t's result
        is identical whether or not later rounds are padded — we publish only rounds 1..t.
        """
        names = self.team_names()
        n = max(t, self._num_rounds())
        rows = [{name: {} for name in names} for _ in range(n)]
        qs = self.game.submissions.filter(round__lte=t).select_related("team")
        for sub in qs:
            if 1 <= sub.round <= n:
                rows[sub.round - 1][sub.team.name] = sub.submitted_levers or {}
        return rows

    # ---- round close: pure re-derivation ----
    @transaction.atomic
    def close_round(self, rnd: int) -> dict:
        names = self.team_names()
        history = api.run_history(names, self._submissions_through(rnd), self.config)

        # Crisis (decision 1): at/after the crisis round, derive per-team crisis_inputs from
        # the pre-crisis state so the R9 beat is LIVE — unless faculty authored them at setup.
        history = self._ensure_crisis_inputs(rnd, names, history)

        # Publish only rounds 1..rnd; later rounds in `history` are speculative padding.
        for rec in history[:rnd]:
            self._store_result(rec)

        self._advance_relationships(history[rnd - 1])

        self.game.current_round = min(rnd + 1, self.game.num_rounds)
        if rnd >= self.game.num_rounds:
            self.game.status = models.GameStatus.COMPLETE
        else:
            self.game.status = models.GameStatus.RUNNING
        # lock on close (faculty can reopen via reopen_round)
        locked = set(self.game.locked_rounds or [])
        locked.add(rnd)
        self.game.locked_rounds = sorted(locked)
        self.game.save(update_fields=["current_round", "status", "locked_rounds"])
        return history[rnd - 1]

    def _ensure_crisis_inputs(self, rnd: int, names: list, history: list) -> list:
        """Derive & persist crisis_inputs at the crisis round (re-derive if R9 is re-closed),
        then re-run so rounds >= crisis_round reflect the damage. Faculty-authored inputs win."""
        crisis_round = int(self.config.get("crisis_round", 9))
        if rnd < crisis_round:
            return history
        if self.config.get("crisis_inputs_source") == "faculty" and self.config.get("crisis_inputs"):
            return history
        need = (rnd == crisis_round) or not self.config.get("crisis_inputs")
        if not need:
            return history
        pre = history[crisis_round - 2]  # round (crisis_round - 1), e.g. round 8
        ci = derive_crisis_inputs(pre, names,
                                  severity=float(self.config.get("crisis_severity", 1.0)))
        self.config = {**self.config, "crisis_inputs": ci, "crisis_inputs_source": "derived"}
        self.game.config_snapshot = self.config
        self.game.save(update_fields=["config_snapshot"])
        # re-run so the crisis actually lands
        return api.run_history(names, self._submissions_through(rnd), self.config)

    def _store_result(self, rec: dict) -> None:
        models.RoundResult.objects.update_or_create(
            game=self.game, round=rec["round"],
            defaults={
                "raw": rec,
                "firms": rec["firms"],
                "scorecard": rec.get("scorecard", {}),
                "equity": rec.get("equity", {}),
                "attribute_state": rec.get("attribute_state", {}),
                "deal_conditioning": rec.get("deal_conditioning", {}),
                "promotion_depth": rec.get("promotion_depth", {}),
                "damage": rec.get("damage", {}),
                "segment_sizes": rec.get("segment_sizes", {}),
                "open_levers": rec.get("open_levers", []),
                "effective_decisions": rec.get("effective_decisions", {}),
            },
        )

    # ---- history reconstruction (engine-shaped) ----
    def history(self) -> list[dict]:
        records = []
        for r in self.game.results.order_by("round"):
            if r.raw:
                records.append(r.raw)
                continue
            records.append({
                "round": r.round,
                "firms": r.firms,
                "scorecard": r.scorecard,
                "equity": r.equity,
                "attribute_state": r.attribute_state,
                "deal_conditioning": r.deal_conditioning,
                "promotion_depth": r.promotion_depth,
                "damage": r.damage,
                "segment_sizes": r.segment_sizes,
                "open_levers": r.open_levers,
                "effective_decisions": r.effective_decisions,
            })
        return records

    def result_for(self, rnd: int) -> dict | None:
        recs = [r for r in self.history() if r["round"] == rnd]
        return recs[0] if recs else None

    def _ensure_result(self, rnd: int) -> dict:
        """Return round `rnd`'s result, computing & storing it on demand WITHOUT advancing
        the game or locking the round. Used by focus groups / advisors (including the
        listening period) so a student reading the state never advances the faculty-paced game.
        """
        rec = self.result_for(rnd)
        if rec is not None:
            return rec
        names = self.team_names()
        history = api.run_history(names, self._submissions_through(rnd), self.config)
        history = self._ensure_crisis_inputs(rnd, names, history)
        for r in history[:rnd]:
            self._store_result(r)
        return history[rnd - 1]

    # ---- relationships (carried AI cache) ----
    def _advance_relationships(self, record: dict) -> None:
        perception = {f: record["firms"][f]["perception"] for f in record["firms"]}
        pool = api.persona_pool()
        for team in self.game.teams.all():
            existing = {
                pr.persona_id: pr
                for pr in self.game.relationships.filter(team=team)
            }
            for p in pool:
                pid, seg = p["id"], p["segment"]
                rev = api.revealed_warmth(perception, team.name, seg)
                pr = existing.get(pid)
                prev = pr.relationship if pr else 0.5
                new_val = api.update_relationship(prev, rev)
                models.PersonaRelationship.objects.update_or_create(
                    game=self.game, team=team, persona_id=pid,
                    defaults={"relationship": new_val, "updated_round": record["round"]},
                )

    def relationships_for(self, team: models.Team) -> dict:
        rels = {pr.persona_id: pr.relationship
                for pr in self.game.relationships.filter(team=team)}
        # seed any missing persona at neutral 0.5
        for p in api.persona_pool():
            rels.setdefault(p["id"], 0.5)
        return rels

    # ---- engagement cap (LOCKED decision: cap per round + diminishing returns) ----
    def _engagement_cap(self) -> int:
        return int(self.config.get("engagement_cap_per_round", 3))

    def _engagement_actions_in_round(self, team: models.Team, rnd: int) -> int:
        fg = self.game.focus_groups.filter(team=team, round=rnd).count()
        ac = self.game.advisor_consults.filter(team=team, round=rnd).count()
        return fg + ac

    def _check_engagement_cap(self, team: models.Team, rnd: int) -> None:
        cap = self._engagement_cap()
        if cap and self._engagement_actions_in_round(team, rnd) >= cap:
            raise ValidationError(
                {"engagement": f"engagement cap reached for round {rnd} "
                               f"({cap} actions max); spamming does not raise the grade"})

    # ---- customer agents ----
    def focus_group(self, rnd: int, team: models.Team, persona_ids: list,
                    prompt: str = "", user=None) -> list:
        self._check_engagement_cap(team, rnd)
        # read-only: computes the round if needed but never advances/locks the game
        rec = self._ensure_result(rnd)
        perception = {f: rec["firms"][f]["perception"] for f in rec["firms"]}
        by_id = api.persona_by_id()
        chosen = [by_id[pid] for pid in persona_ids if pid in by_id]
        rels = self.relationships_for(team)
        responses = api.focus_group(perception, team.name, chosen, rels, prompt=prompt)
        session = models.FocusGroupSession.objects.create(
            game=self.game, round=rnd, team=team, persona_ids=list(persona_ids),
            prompt=prompt or "", responses=responses, created_by=user,
        )
        return session.responses

    # ---- advisors ----
    def consult_advisors(self, rnd: int, team: models.Team,
                         decision_under_review: dict, user=None) -> list:
        self._check_engagement_cap(team, rnd)
        rec = self._ensure_result(rnd)  # read-only compute; never advances/locks
        perception = {f: rec["firms"][f]["perception"] for f in rec["firms"]}
        # advisor context must be EXACTLY {stated_warmth, eq, trust} (verified signature)
        context = {
            "stated_warmth": api.segment_stated_warmth(perception, team.name),
            "eq": rec["equity"].get(team.name),
            "trust": rec["attribute_state"].get(team.name, {}).get("TRUST"),
        }
        stances = api.advise(decision_under_review, context)
        usage = api.build_usage_record(team.name, rnd, stances, decision_under_review)
        models.AdvisorConsultation.objects.create(
            game=self.game, round=rnd, team=team,
            decision_under_review=decision_under_review, stances=stances,
            usage_record=usage, created_by=user,
        )
        return stances

    # ---- scorecard ----
    def scorecard(self) -> list:
        history = self.history()
        if not history:
            return []  # no closed rounds yet — the engine requires >=1 round record
        report = api.build_report(history)
        weights = self.config.get("scorecard_weights")
        if weights:  # default weights (0.25 each) reproduce the engine's overall exactly
            report = apply_scorecard_weights(report, weights)
        return report

    # ---- engagement (for the defense grade) ----
    def engagement_counts(self) -> dict:
        """Raw per-team engagement action counts (faculty dashboard view)."""
        counts = {n: 0 for n in self.team_names()}
        for fg in self.game.focus_groups.select_related("team"):
            counts[fg.team.name] = counts.get(fg.team.name, 0) + 1
        for ac in self.game.advisor_consults.select_related("team"):
            counts[ac.team.name] = counts.get(ac.team.name, 0) + 1
        return counts

    def engagement(self) -> dict:
        """Saturated effective engagement fed to build_defense (decision 3).

        The per-round cap is enforced at action time (`_check_engagement_cap`); here the
        raw game totals get a hyperbolic diminishing-returns transform so spamming stops
        paying. Applied in the app layer so the locked defense module is never edited.
        """
        half = float(self.config.get("engagement_half_saturation", 6.0))
        return saturating_engagement(self.engagement_counts(), half_saturation=half)

    # ---- board defense ----
    def board_defense(self, team: models.Team, defense_text: str) -> dict:
        history = self.history()
        # Re-weight the scorecard overalls by faculty weights so build_defense's outcome term
        # is consistent with the student-facing scorecard (default weights => no change).
        weights = self.config.get("scorecard_weights")
        if weights:
            history = deepcopy(history)
            for rec in history:
                for firm in rec.get("scorecard", {}).get("firms", {}).values():
                    if "normalized" in firm:
                        firm["overall"] = reweight_overall(firm["normalized"], weights)
        decisions_by_round = [rec.get("effective_decisions", {}) for rec in history]
        det = api.build_defense(history, decisions_by_round, self.engagement())

        focus_groups = list(self.game.focus_groups.select_related("team").values(
            "round", "team__name", "persona_ids", "prompt", "responses"))
        focus_groups = [{**f, "team": f.pop("team__name")} for f in focus_groups]
        advisor_consults = list(self.game.advisor_consults.select_related("team").values(
            "round", "team__name", "decision_under_review", "stances"))
        advisor_consults = [{**a, "team": a.pop("team__name")} for a in advisor_consults]

        packet = api.assemble_defense_packet(history, decisions_by_round, team.name,
                                             focus_groups, advisor_consults)
        cache = _DbGradeCache()
        prose = api.grade_written_defense(defense_text, packet, cache=cache)
        combined = api.combine_report(det.get(team.name, {}), prose)

        models.DefenseSubmission.objects.update_or_create(
            game=self.game, team=team,
            defaults={
                "defense_text": defense_text,
                "deterministic_grade": det.get(team.name, {}),
                "prose_grade": prose,
                "combined": combined,
            },
        )
        return combined


class _DbGradeCache(dict):
    """Dict-like cache backed by the GradeCache table so a regrade after restart matches."""

    def __contains__(self, key):
        if super().__contains__(key):
            return True
        return models.GradeCache.objects.filter(input_hash=str(key)).exists()

    def __getitem__(self, key):
        if super().__contains__(key):
            return super().__getitem__(key)
        row = models.GradeCache.objects.get(input_hash=str(key))
        val = {"prose_dimensions": row.prose_dimensions,
               "prose_overall": row.prose_overall,
               "justification": row.justification}
        super().__setitem__(key, val)
        return val

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        models.GradeCache.objects.update_or_create(
            input_hash=str(key),
            defaults={"prose_dimensions": value.get("prose_dimensions", {}),
                      "prose_overall": value.get("prose_overall", 0.0),
                      "justification": value.get("justification", "")},
        )
