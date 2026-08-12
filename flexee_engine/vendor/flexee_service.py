"""Flexee Marketing Management — reference orchestration layer (framework-free).

A thin spine that ties the verified backend together end to end: it holds a game, takes
per-round submissions, closes rounds by re-running the pure runner over the submissions of
record, and exposes the focus-group / advisor / scorecard / board-defense operations wired to
the injected model client. It is deliberately in-memory and UI-agnostic — a working core to
build the UI and persistence onto, not a finished app.

Design commitments (see the handoff & the data-contracts doc):
  * The runner is PURE. State is never carried by hand — every round close re-derives the whole
    history from the stored submissions (rounds 1..t). No manual EQ/crisis/DC bookkeeping.
  * The AI never moves a number. focus_group / advisors read the deterministic state and render it.
  * Segment labels and raw scores never leave the service in student-facing output.
  * Persistence is a seam: swap `InMemoryStore` for a DB-backed store with the same methods.

Confirm the exact imported signatures against the real modules and their test_step*.py — a couple
of helper names may differ slightly from the intended contract; the seams below are where to check.

KNOWN SEAMS TO CONFIRM AGAINST THE SHIPPING MODULES (found during review):
  1. run_game_with_faculty's effective ("built") decisions. board_defense needs the baseline-filled
     decision per round per team. close_round now captures them whether the runner returns
     (history, built), embeds record["effective_decisions"], or neither (in which case
     _decisions_by_round raises a clear error rather than a KeyError inside build_defense). Confirm
     which shape the shipping runner uses.
  2. advisors.build_usage_record — dashboard metadata only; consult_advisors now falls back to a small
     dict if the helper is absent (the engagement SCORE is counted independently).
  3. defense.assemble_defense_packet — REQUIRED for the written-prose grade, and only invoked when a
     model_client is set. Confirm it exists under this name; if it differs, fix the one call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# --- Backend modules (verified, locked) ---------------------------------------------------------
from game_faculty import run_game_with_faculty
from faculty import DEFAULT_FACULTY_CONFIG
from crisis import CrisisParams
from report import build_report
from defense import assemble_defense_packet, build_defense
from defense_grader import grade_written_defense, combine_report
import personas
import customer_agents
import advisors
import live_voice
import advisor_voice
from flexee_calibrated_config import (
    CALIBRATED_PARAMS, CALIBRATED_EQUITY_PARAMS, CALIBRATED_PROMOTION_PARAMS,
    CALIBRATED_CRISIS_DAMAGE_BASE,
)

SEGS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")


# --- Persistence seam ---------------------------------------------------------------------------
# Swap this for a DB-backed implementation with the same method names (see the schema doc).
class InMemoryStore:
    def __init__(self):
        self.submissions: dict[int, dict[str, dict]] = {}       # round -> team -> decision
        self.round_results: dict[int, dict] = {}               # round -> history record
        self.effective_decisions: dict[int, dict[str, dict]] = {}  # round -> team -> baseline-filled decision
        self.relationships: dict[str, dict[str, float]] = {}   # team -> persona_id -> relationship
        self.focus_groups: list[dict] = []                     # transcripts
        self.advisor_consults: list[dict] = []                 # consultations
        self.defenses: dict[str, dict] = {}                    # team -> defense record

    def save_submission(self, rnd, team, decision):
        self.submissions.setdefault(rnd, {})[team] = decision

    def get_submissions_through(self, t):
        return [self.submissions.get(r, {}) for r in range(1, t + 1)]


# --- Game configuration -------------------------------------------------------------------------
@dataclass
class GameConfig:
    faculty_config: Any = DEFAULT_FACULTY_CONFIG
    params: Any = CALIBRATED_PARAMS
    equity_params: Any = CALIBRATED_EQUITY_PARAMS
    promotion_params: Any = CALIBRATED_PROMOTION_PARAMS
    crisis_round: int = 9
    crisis_damage_base: float = CALIBRATED_CRISIS_DAMAGE_BASE
    growth_rates: Optional[dict] = None                        # None -> module default
    crisis_inputs: Optional[dict] = None                       # team -> {exposure,preempt,response}

    def crisis_params(self) -> CrisisParams:
        return CrisisParams(crisis_round=self.crisis_round, damage_base=self.crisis_damage_base,
                            trust_fraction=0.5, prod_fraction=0.3, eq_fraction=0.2,
                            lower_bound=0.0, upper_bound=100.0)


# --- The service --------------------------------------------------------------------------------
class FlexeeGame:
    def __init__(self, team_names, config: Optional[GameConfig] = None,
                 model_client: Optional[Callable] = None, store: Optional[InMemoryStore] = None):
        self.teams = list(team_names)
        self.config = config or GameConfig()
        self.store = store or InMemoryStore()
        self.model_client = model_client                       # None -> layers use their safe mock
        self._pool = list(personas.POOL)                       # internal: has segments (never exposed)
        self._pool_by_id = {p["id"]: p for p in self._pool}
        self._seg_of = {p["id"]: p["segment"] for p in self._pool}
        for t in self.teams:
            self.store.relationships.setdefault(t, {p["id"]: 0.5 for p in self._pool})
        # generators: live if a model client was supplied, else the modules' own mock defaults
        self._persona_gen = (live_voice.live_persona_generator(model_client, self._pool_by_id)
                             if model_client else None)
        self._advisor_gen = (advisor_voice.live_advisor_generator(model_client)
                             if model_client else None)

    # ---- round lifecycle ----
    def open_levers(self, rnd: int):
        """Which levers the student screen should show this round."""
        # confirm the exact helper name in faculty.py; intended contract:
        from faculty import open_levers_for_round
        return open_levers_for_round(rnd, self.config.faculty_config)

    def submit(self, rnd: int, team: str, decision: dict):
        """Store a team's submission for a round (only the open-lever fields)."""
        if team not in self.teams:
            raise ValueError(f"unknown team {team!r}")
        self.store.save_submission(rnd, team, dict(decision))

    def close_round(self, rnd: int) -> dict:
        """Re-run the game over rounds 1..rnd from stored submissions; publish & persist round rnd."""
        subs = self.store.get_submissions_through(rnd)
        # ensure every round carries exactly the team set (empty submissions in listening rounds)
        subs = [{t: row.get(t, {}) for t in self.teams} for row in subs]
        result = run_game_with_faculty(
            self.teams, subs, self.config.faculty_config,
            params=self.config.params, equity_params=self.config.equity_params,
            promotion_params=self.config.promotion_params,
            crisis_params=self.config.crisis_params(), crisis_inputs=self.config.crisis_inputs,
        )
        # The runner builds the baseline-filled ("effective") decisions internally. Confirm HOW the
        # shipping run_game_with_faculty exposes them and this captures all three plausible shapes:
        #   (a) it returns (history, built)         -> built[rnd-1] is {team: decision}
        #   (b) each record embeds them             -> record["effective_decisions"]
        #   (c) neither                             -> stays empty; board_defense reconstructs / errors clearly
        built = None
        if isinstance(result, tuple):
            history, built = result[0], result[1]
        else:
            history = result
        record = history[rnd - 1]
        self.store.round_results[rnd] = record
        if built is not None:
            self.store.effective_decisions[rnd] = built[rnd - 1]
        elif isinstance(record, dict) and "effective_decisions" in record:
            self.store.effective_decisions[rnd] = record["effective_decisions"]
        self._advance_relationships(record)                    # carry persona relationships
        return self._student_round_view(record)

    def history(self):
        return [self.store.round_results[r] for r in sorted(self.store.round_results)]

    # ---- customer agents ----
    def focus_group(self, rnd: int, team: str, persona_ids, prompt: Optional[str] = None) -> list:
        rec = self.store.round_results[rnd]
        perception = {f: rec["firms"][f]["perception"] for f in rec["firms"]}
        chosen = [{"id": pid, "name": self._pool_by_id[pid]["name"],
                   "segment": self._seg_of[pid], "idiosyncrasy": self._pool_by_id[pid]["idiosyncrasy"]}
                  for pid in persona_ids]
        rels = self.store.relationships[team]
        kwargs = {"generator": self._persona_gen} if self._persona_gen else {}
        responses = customer_agents.focus_group(perception, team, chosen, rels, **kwargs)
        public = self._strip_focus_group(responses)            # ensure no segment/raw leaks
        self.store.focus_groups.append({"round": rnd, "team": team, "persona_ids": list(persona_ids),
                                        "prompt": prompt, "responses": public})
        return public

    # ---- advisors ----
    def consult_advisors(self, rnd: int, team: str, decision_under_review: dict) -> list:
        rec = self.store.round_results[rnd]
        perception = {f: rec["firms"][f]["perception"] for f in rec["firms"]}
        stated = {s: customer_agents.stated_warmth(
            customer_agents.revealed_warmth(perception, team, s), s, 0.0) for s in SEGS}
        context = {"stated_warmth": stated,
                   "eq": rec["equity"][team],
                   "trust": rec["attribute_state"][team]["TRUST"]}
        kwargs = {"generator": self._advisor_gen} if self._advisor_gen else {}
        stances = advisors.advise(decision_under_review, context, **kwargs)
        # build_usage_record is faculty-dashboard metadata only; the engagement SCORE is counted
        # independently in engagement(), so a missing helper must not block the advisor path.
        if hasattr(advisors, "build_usage_record"):
            usage = advisors.build_usage_record(team, rnd, stances, decision_under_review)
        else:
            usage = {"team": team, "round": rnd, "n_stances": len(stances)}
        self.store.advisor_consults.append({"round": rnd, "team": team,
                                            "decision_under_review": decision_under_review,
                                            "stances": stances, "usage": usage})
        return stances

    # ---- scorecard ----
    def scorecard(self):
        return build_report(self.history())

    # ---- engagement (for the defense grade) ----
    def engagement(self) -> dict:
        eng = {t: 0 for t in self.teams}
        for fg in self.store.focus_groups:
            eng[fg["team"]] += 1
        for ac in self.store.advisor_consults:
            eng[ac["team"]] += 1
        return eng

    # ---- board defense ----
    def board_defense(self, team: str, defense_text: str) -> dict:
        history = self.history()
        decisions_by_round = self._decisions_by_round()        # baseline-filled, from close_round capture
        det = build_defense(history, decisions_by_round, self.engagement())
        packet = assemble_defense_packet(history, decisions_by_round, team,
                                         self.store.focus_groups, self.store.advisor_consults)
        prose = grade_written_defense(defense_text, packet, self.model_client) if self.model_client else None
        combined = combine_report(det[team], prose)
        self.store.defenses[team] = combined
        return combined

    # ---- internals ----
    def _decisions_by_round(self):
        """The baseline-filled ('effective') decision per round per team, in round order 1..N.

        build_defense reads decisions_by_round[r-1][team]['emphasis'] and ['positioning'] for every
        live round, so these must be present and complete — this is exactly the seam that KeyErrors
        if the runner's effective decisions never reach here. close_round captures them (see the three
        shapes noted there). If any round is missing, fail loudly with guidance instead of a cryptic
        KeyError deep inside build_defense: the fix is to confirm how the shipping run_game_with_faculty
        exposes its built decisions and adjust the capture in close_round.
        """
        rounds = sorted(self.store.round_results)
        missing = [r for r in rounds if r not in self.store.effective_decisions]
        if missing:
            raise RuntimeError(
                "effective (baseline-filled) decisions were not captured for rounds "
                f"{missing}. board_defense needs them for every round. Confirm how the shipping "
                "run_game_with_faculty returns/embeds its built decisions and update close_round's "
                "capture block (it already handles a (history, built) tuple and a record-embedded "
                "'effective_decisions' key). As a last resort, reconstruct them with the faculty "
                "module's build_effective over the stored submissions.")
        return [self.store.effective_decisions[r] for r in rounds]

    def _advance_relationships(self, record):
        perception = {f: record["firms"][f]["perception"] for f in record["firms"]}
        for t in self.teams:
            rels = self.store.relationships[t]
            for pid in rels:
                rev = customer_agents.revealed_warmth(perception, t, self._seg_of[pid])
                rels[pid] = customer_agents.update_relationship(rels[pid], rev)

    def _student_round_view(self, record):
        # a render-ready per-team slice; extend as your UI needs
        return {"round": record["round"], "segment_sizes": record["segment_sizes"],
                "open_levers": record.get("open_levers"),
                "teams": {t: {"share": record["firms"][t]["total_market_share"],
                              "revenue": record["firms"][t]["revenue"],
                              "contribution": record["firms"][t]["contribution"],
                              "scorecard": record["scorecard"]["firms"].get(t)} for t in self.teams}}

    @staticmethod
    def _strip_focus_group(responses):
        # defense-in-depth: the module already omits these, but never trust upstream with student output
        safe = []
        for r in responses:
            safe.append({k: r[k] for k in ("persona_id", "persona_name", "stated_warmth",
                                            "relationship", "text") if k in r})
        return safe


if __name__ == "__main__":
    # illustrative flow (runs with the modules' mock voices when no model_client is passed)
    game = FlexeeGame(["A", "B", "C", "D"])
    for rnd in range(1, 5):
        for t in game.teams:
            if rnd >= 4:  # takeover: allocation + emphasis open
                game.submit(rnd, t, {"brand": 5, "perf": 2, "research": 3,
                                     "emphasis": (0.2, 0.2, 0.2, 0.2, 0.2)})
        view = game.close_round(rnd)
        print("round", rnd, "closed; A share", round(view["teams"]["A"]["share"], 4))
    print("focus group:", game.focus_group(4, "A", ["leg_marcus", "fun_sarah"])[:1])
