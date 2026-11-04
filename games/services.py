"""Service layer — the DB-backed version of the reference flexee_service.py.

Ties the persisted submissions to the pure engine. The runner is PURE: every round
close re-derives the whole history from the submissions of record (rounds 1..t), so no
simulation state is ever carried by hand. The AI never moves a number — it renders the
deterministic state that the engine computed.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone as dj_tz
from rest_framework.exceptions import ValidationError

from flexee_engine import api
from . import models
from .deadlines import clock_runs, now_utc, round_deadline, round_opens
from .enrichment_overlay import (
    LINE_EXT_CARRY_COST,
    LINE_EXT_LAUNCH_COST,
    apply_line_extension,
    crisis_inputs_with_line_extension,
    student_research_sheet,
)
from .validators import validate_decision

log = logging.getLogger("flexee.service")

SEGS = api.SEGS


SCORECARD_DIMS = ("brand_health", "customer_value", "market_position", "financial")

# The engine's decision validation rejects unknown fields, so only these reach the runner.
# Group B levers (channel/message/line_extension) are app-layer: channel/message are threaded
# in via the seam, line_extension is a post-round overlay.
ENGINE_DECISION_FIELDS = ("brand", "perf", "research", "positioning", "emphasis", "price", "promotion")
GROUP_B_LEVERS = ("channel", "message", "line_extension")


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
        # Engine levers (takeover-gated by the faculty config) + app-layer Group B levers
        # scheduled separately (the engine's lever_open_round must stay the 5 base levers).
        base = list(api.open_levers_for_round(rnd, self.config))
        sched = self.config.get("lever_schedule", {}) or {}
        extras = [l for l in GROUP_B_LEVERS if rnd >= int(sched.get(l, 999))]
        return base + extras

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
        if team.reference_path:
            raise ValidationError({"team": "a reference firm plays a fixed path; nobody can decide for it"})
        # A live game's round closes at its deadline (23:59 game time). Between the deadline
        # and the sweep that closes it, a change is refused rather than quietly accepted.
        if clock_runs(self.game):
            deadline = round_deadline(self.game, rnd)
            if deadline is not None and now_utc() > deadline:
                raise ValidationError(
                    {"round": f"Round {rnd} has closed, so this change was not saved."})
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

        Rounds 1..t carry the stored submissions, with any team that missed one filled in
        with that round's cohort average (see `_fill_missing_with_cohort_average`); future
        rounds are left empty (the runner fills them from the faculty baseline). The runner
        is sequential/pure, so round t's result is identical whether or not later rounds are
        padded — we publish only rounds 1..t.
        """
        names = self.team_names()
        n = max(t, self._num_rounds())
        rows = [{name: {} for name in names} for _ in range(n)]
        qs = self.game.submissions.filter(round__lte=t).select_related("team")
        for sub in qs:
            if 1 <= sub.round <= n:
                lv = sub.submitted_levers or {}
                # Only engine decision fields reach the runner (Group B levers are app-layer).
                rows[sub.round - 1][sub.team.name] = {k: lv[k] for k in ENGINE_DECISION_FIELDS if k in lv}
        self._fill_missing_with_cohort_average(rows[:t])
        return rows

    @staticmethod
    def _fill_missing_with_cohort_average(played_rows: list[dict]) -> None:
        """A team that misses a round's submission entirely gets that round's COHORT
        AVERAGE (mean of every field the submitting teams actually set) instead of a hard
        reset to the fixed engine baseline — a missed round should track the class's
        median strategy, not an unrelated fixed number, so it's neither a free pass nor an
        unfair penalty. Mutates the round dicts in place; skips a round if nobody submitted
        (listening period, or everyone missed it) since there's nothing to average.
        """
        for round_row in played_rows:
            submitted = [dec for dec in round_row.values() if dec]
            missing_teams = [team for team, dec in round_row.items() if not dec]
            if not submitted or not missing_teams:
                continue
            avg: dict = {}
            for field in ENGINE_DECISION_FIELDS:
                if field == "emphasis":
                    continue
                values = [dec[field] for dec in submitted if field in dec]
                if values:
                    avg[field] = sum(values) / len(values)
            emphasis_lists = [dec["emphasis"] for dec in submitted if "emphasis" in dec]
            if emphasis_lists:
                avg["emphasis"] = [sum(v[i] for v in emphasis_lists) / len(emphasis_lists)
                                   for i in range(len(emphasis_lists[0]))]
            for team in missing_teams:
                round_row[team] = avg

    # ---- Group B: app-layer channel/message maps + line-extension state ----
    def _group_b_maps(self, t: int):
        """Per-round {team: channel_weights} and {team: message} from stored submissions.
        Returns (channel_by_round, message_by_round) or None when unused (→ neutral engine)."""
        n = max(t, self._num_rounds())
        ch = [dict() for _ in range(n)]
        msg = [dict() for _ in range(n)]
        for sub in self.game.submissions.filter(round__lte=t).select_related("team"):
            i = sub.round - 1
            if not (0 <= i < n):
                continue
            lv = sub.submitted_levers or {}
            if isinstance(lv.get("channel"), dict):
                ch[i][sub.team.name] = lv["channel"]
            if lv.get("message"):
                msg[i][sub.team.name] = lv["message"]
        return (ch if any(ch) else None, msg if any(msg) else None)

    def _line_ext_info(self, t: int) -> dict:
        """team -> {launch_round, positioning} for the first round the team launched (sticky)."""
        info: dict = {}
        for sub in self.game.submissions.filter(round__lte=t).order_by("round").select_related("team"):
            le = (sub.submitted_levers or {}).get("line_extension")
            if isinstance(le, dict) and le.get("launch") and sub.team.name not in info:
                info[sub.team.name] = {"launch_round": sub.round, "positioning": float(le.get("positioning", 90.0))}
        return info

    def _apply_line_extension(self, history: list, info: dict | None = None) -> list:
        """Overlay the functional line onto each record: net volume per segment, launch and
        carry costs, refreshed shares and a recomputed scorecard (see enrichment_overlay).
        App-layer; the engine is untouched, and a game with no launch is returned as-is."""
        if info is None:
            info = self._line_ext_info(self._num_rounds())
        return apply_line_extension(
            history, info,
            budget=float(self.config.get("round_budget", 10.0)),
            launch_cost=float(self.config.get("line_extension_launch_cost", LINE_EXT_LAUNCH_COST)),
            carry_cost=float(self.config.get("line_extension_carry_cost", LINE_EXT_CARRY_COST)),
        )

    def _compute(self, rnd: int) -> list:
        """Single compute path: engine run with Group B channel/message threaded in, then the
        line-extension overlay. Used everywhere history is (re-)derived.

        When crisis inputs exist (derived or faculty-authored), each launched team's line
        extension credit is folded into its crisis `response` for THIS run only. The stored
        config keeps the unadjusted inputs, so re-deriving never compounds the credit."""
        names = self.team_names()
        ch, msg = self._group_b_maps(rnd)
        info = self._line_ext_info(self._num_rounds())
        cfg = self.config
        if info and cfg.get("crisis_inputs"):
            cfg = {**cfg, "crisis_inputs": crisis_inputs_with_line_extension(
                cfg["crisis_inputs"], info, int(cfg.get("crisis_round", 9)))}
        history = api.run_history(names, self._submissions_through(rnd), cfg,
                                  team_channel_by_round=ch, team_message_by_round=msg)
        return self._apply_line_extension(history, info)

    # ---- round close: pure re-derivation ----
    CLAIM_STALE = timedelta(minutes=15)

    def claim_close(self) -> bool:
        """One claim per close: a single atomic UPDATE taken by every close path (the Advance
        button, the scheduled sweep). A second close while one is running is refused; a claim
        older than 15 minutes is treated as dead."""
        now = dj_tz.now()
        taken = models.Game.objects.filter(pk=self.game.pk).filter(
            Q(close_claimed_at__isnull=True) | Q(close_claimed_at__lt=now - self.CLAIM_STALE)
        ).update(close_claimed_at=now)
        return taken == 1

    def release_close(self) -> None:
        models.Game.objects.filter(pk=self.game.pk).update(close_claimed_at=None)
        self.game.close_claimed_at = None

    def close_round(self, rnd: int, source: str = "faculty") -> dict:
        """Close round `rnd` for every team in one step, and record the close (who closed it,
        how long it took, how many firms had submitted). `source` is "faculty" (Advance) or
        "deadline" (the scheduled sweep or a page load after the deadline)."""
        if not self.claim_close():
            raise ValidationError({"round": f"Round {rnd} is already closing; try again in a moment."})
        started = dj_tz.now()
        firms = self.game.teams.count()
        submitted = self.game.submissions.filter(round=rnd).values("team_id").distinct().count()
        decisions_open = bool(self.open_levers(rnd))
        opened, deadline = round_opens(self.game, rnd), round_deadline(self.game, rnd)
        try:
            result = self._close_round_locked(rnd)
        except Exception:
            self._record_close(rnd, source, "failed", started, firms, submitted, decisions_open,
                               opened, deadline, error_code="other")
            raise
        finally:
            self.release_close()
        self._record_close(rnd, source, "succeeded", started, firms, submitted, decisions_open,
                           opened, deadline)
        return result

    def _record_close(self, rnd, source, status, started, firms, submitted, decisions_open,
                      opened, deadline, error_code=None) -> None:
        """Never lets a logging failure fail the close."""
        try:
            done = dj_tz.now()
            models.RoundCloseLog.objects.create(
                game_id_ref=self.game.pk, course=self.game.name, round=rnd, source=source,
                status=status, error_code=error_code, opened_at=opened, deadline_at=deadline,
                started_at=started, completed_at=done, seconds=(done - started).total_seconds(),
                firms=firms, firms_submitted=submitted,
                firms_carried_forward=max(firms - submitted, 0) if decisions_open else 0,
                practice=self.game.is_practice,
            )
        except Exception:  # pragma: no cover - logging must not break a close
            log.exception("could not record round close for game %s round %s", self.game.pk, rnd)

    @transaction.atomic
    def _close_round_locked(self, rnd: int) -> dict:
        names = self.team_names()
        history = self._compute(rnd)

        # Crisis (decision 1): at/after the crisis round, derive per-team crisis_inputs from
        # the pre-crisis state so the R9 beat is LIVE — unless faculty authored them at setup.
        history = self._ensure_crisis_inputs(rnd, names, history)

        # Publish only rounds 1..rnd; later rounds in `history` are speculative padding.
        for rec in history[:rnd]:
            self._store_result(rec)

        self._advance_relationships(history[rnd - 1])
        self._carry_forward_forecasts(rnd)  # a team that skipped this round's forecast repeats its last one
        self._score_forecasts(rnd)  # Group C: grade this round's predictions against the result

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
        return self._compute(rnd)

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

    # ---- financial statements (reporting overlay; closed rounds only) ----
    def financials(self) -> dict:
        """Per-team three-statement financials over the closed-round history.

        Composes over engine outputs (revenue, promotion, budget split) — no new
        market math. A single `financials_budget_scale` maps abstract budget units to
        dollars so magnitudes read sensibly; all constants are calibrate-later config.
        """
        scale = float(self.config.get("financials_budget_scale", 10.0))
        promo_rate = float(self.config.get("promo_cost_rate", 0.30))
        hist = self.history()
        out: dict = {}
        for t in self.team_names():
            mkts = []
            for rec in hist:
                firm = (rec.get("firms") or {}).get(t)
                if not firm:
                    continue
                dec = (rec.get("effective_decisions") or {}).get(t, {})
                rev = float(firm.get("revenue", 0.0))
                tv = float(firm.get("total_volume", 0.0))
                promo = float(firm.get("promotion", 0.0))
                # The line extension's launch/carry cost is launch marketing: expensed with
                # performance spend so the accounts show that the functional line cost money.
                le_cost = float((firm.get("line_extension") or {}).get("cost", 0.0))
                mkts.append({
                    "gross_revenue": str(round(rev * scale, 4)),
                    "promo_allow": str(round(tv * promo_rate * promo * scale, 4)),
                    "brand_spend": str(round(float(dec.get("brand", 0.0)) * scale, 4)),
                    "perf_spend": str(round((float(dec.get("perf", 0.0)) + le_cost) * scale, 4)),
                    "research_spend": str(round(float(dec.get("research", 0.0)) * scale, 4)),
                })
            out[t] = api.financials_statements(mkts)
        return out

    # ---- market research (Group C: rendering over state + budget ledger) ----
    def _research_ctx(self, team_name: str):
        hist = self.history()
        if not hist:
            return None
        rec = hist[-1]
        firms = rec.get("firms") or {}
        if team_name not in firms:
            return None
        return {
            "perception_by_firm": {f: firms[f].get("perception", {}) for f in firms},
            "firm": team_name,
            "team_positioning": float((rec.get("effective_decisions") or {}).get(team_name, {}).get("positioning", 35.0)),
            "round": rec["round"],
        }

    def research_budget(self, team: models.Team) -> dict:
        total = float(self.config.get("research_budget", 20.0))
        spent = sum(c.cost for c in self.game.research.filter(team=team))
        return {"total": total, "spent": round(spent, 2), "remaining": round(total - spent, 2)}

    _WHERE_TO_GROW_COST = {"stated": 3.0, "behavioral": 6.0}

    @transaction.atomic
    def commission_research(self, team: models.Team, study: str, mode: str, user=None) -> dict:
        import research as r
        # Serialize commissions per team so two concurrent requests cannot both pass the
        # budget check and overspend (select_for_update is a no-op on SQLite, real on Postgres).
        models.Team.objects.select_for_update().get(pk=team.pk)
        if mode not in ("stated", "behavioral"):
            raise ValidationError({"mode": "must be 'stated' or 'behavioral'"})
        ctx = self._research_ctx(team.name)
        if ctx is None:
            raise ValidationError({"detail": "no closed round yet — commission research after round 1 closes"})
        if study == "where_to_grow":
            cost = self._WHERE_TO_GROW_COST[mode]
        elif study in r.STUDY_CATALOG:
            spec = r.STUDY_CATALOG[study]
            cost = float(spec["cost_behavioral"] if mode == "behavioral" else spec["cost_stated"])
        else:
            raise ValidationError({"study": "unknown study"})
        bud = self.research_budget(team)
        if cost > bud["remaining"] + 1e-9:
            raise ValidationError({"detail": f"research budget exhausted (need {cost}, have {bud['remaining']})"})
        if study == "where_to_grow":
            sheet = {"ranking": api.where_to_grow(ctx, mode)}
        else:
            sheet = api.render_study(study, mode, ctx)
        # The full sheet (with engine truth) is stored for faculty/audit; students only ever
        # receive the reported values — otherwise a cheap stated study leaks behavioral truth.
        models.ResearchCommission.objects.create(
            game=self.game, round=ctx["round"], team=team, study=study, mode=mode,
            cost=cost, sheet=sheet, created_by=user)
        return {"study": study, "mode": mode, "cost": cost, "round": ctx["round"],
                "sheet": student_research_sheet(sheet), "budget": self.research_budget(team)}

    def research_history(self, team: models.Team) -> list:
        return [{"round": c.round, "study": c.study, "mode": c.mode, "cost": c.cost,
                 "sheet": student_research_sheet(c.sheet)}
                for c in self.game.research.filter(team=team).order_by("-created_at")]

    # ---- forecast loop (Group C) ----
    def submit_forecast(self, rnd: int, team: models.Team, metric: str, predicted: float,
                        note: str = "", user=None) -> dict:
        if metric not in ("own_share", "growth_share"):
            raise ValidationError({"metric": "must be 'own_share' or 'growth_share'"})
        obj, _ = models.Forecast.objects.update_or_create(
            game=self.game, round=rnd, team=team,
            defaults={"metric": metric, "predicted": float(predicted), "note": note, "created_by": user})
        return {"round": rnd, "metric": obj.metric, "predicted": obj.predicted}

    def _carry_forward_forecasts(self, rnd: int) -> None:
        """A team that never called submit_forecast for this round gets its most recent
        prior forecast (same metric/predicted) copied forward, so a missed forecast still
        gets scored against this round's real outcome instead of silently vanishing."""
        already = set(self.game.forecasts.filter(round=rnd).values_list("team_id", flat=True))
        for team in self.game.teams.all():
            if team.id in already:
                continue
            prior = self.game.forecasts.filter(team=team, round__lt=rnd).order_by("-round").first()
            if prior is None:
                continue
            models.Forecast.objects.create(
                game=self.game, round=rnd, team=team, metric=prior.metric,
                predicted=prior.predicted, note=prior.note,
            )

    def _score_forecasts(self, rnd: int) -> None:
        rec = self.result_for(rnd)
        if not rec:
            return
        firms = rec.get("firms") or {}
        for fc in self.game.forecasts.filter(round=rnd, actual__isnull=True).select_related("team"):
            firm = firms.get(fc.team.name)
            if not firm:
                continue
            share = float(firm.get("total_market_share", 0.0) if fc.metric == "own_share"
                          else firm.get("growth_segment_share", 0.0))
            actual = share * 100.0  # percent, matching the student's entry
            denom = max(abs(actual), 5.0)
            fc.actual = actual
            fc.accuracy = max(0.0, 1.0 - abs(fc.predicted - actual) / denom)
            fc.save(update_fields=["actual", "accuracy"])

    def forecasts_for(self, team: models.Team) -> dict:
        rows = [{"round": f.round, "metric": f.metric, "predicted": f.predicted,
                 "actual": f.actual, "accuracy": f.accuracy, "note": f.note}
                for f in self.game.forecasts.filter(team=team).order_by("round")]
        scored = [r["accuracy"] for r in rows if r["accuracy"] is not None]
        avg = round(sum(scored) / len(scored), 3) if scored else None
        return {"rows": rows, "avg_accuracy": avg}

    def _ensure_result(self, rnd: int) -> dict:
        """Return round `rnd`'s result, computing & storing it on demand WITHOUT advancing
        the game or locking the round. Used by focus groups / advisors (including the
        listening period) so a student reading the state never advances the faculty-paced game.
        """
        rec = self.result_for(rnd)
        if rec is not None:
            return rec
        names = self.team_names()
        history = self._compute(rnd)
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
