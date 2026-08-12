# Flexee Marketing Management — Backend

Django + DRF backend for the **Flexee Marketing Management** simulation. This is the
"developer's layer" the handoff docs describe: **accounts/auth, a database, the API, and
the LLM voice layer** — wrapped around the product team's **locked, verified simulation
engine**. It implements the persistence schema (Part B) and per-screen data contracts
(Part A) from *Flexee UI Contracts & Persistence*.

## Architecture at a glance

```
config/            Django project (settings, urls)
accounts/          Custom User (faculty | student), token auth, register/login/me
games/             Domain: models, services, serializers, views, validators, permissions
flexee_engine/     The engine SEAM — the only place Django touches the simulation
    api.py         Single import point (real engine if present, else dev stub)
    _real.py       Adapter to the verified engine (activates when vendor/ is populated)
    _stub.py       Deterministic dev stub (NOT calibrated) so the API runs today
    django_store.py  DjangoStore: drop-in for InMemoryStore, backed by the DB
    spine.py       make_flexee_game(): wires FlexeeGame + DjangoStore when engine present
    model_client.py  LLM adapter (Anthropic/OpenAI/offline) — vendored from the handoff
    vendor/        <-- drop the verified engine modules (+ flexee_service.py) here
```

**Core orchestration rule (from the docs):** the runner is *pure*. Each round-close
re-derives the whole history from the stored submissions for rounds 1..t. Submissions are
the source of truth; `RoundResult` is a derived cache. No simulation state is carried by
hand. The AI never moves a number — it renders the deterministic state.

## The engine seam (important)

The verified engine now lives in `flexee_engine/vendor/` (18 modules + `flexee_calibrated_config`),
so `flexee_engine.api.ENGINE_MODE == "real"` and the app runs on the **calibrated engine**
(full engine suite: 731 green). If the vendor modules are removed, `api.py` falls back to a
deterministic **dev stub** in the same contract shapes. `GET /api/health/` reports which is
active (`"engine_mode": "real" | "stub"`).

### Shipping signatures the adapter handles (differ from the reference `flexee_service.py`)

Confirmed against the shipping modules and handled in `_real.py` (the Django app drives this
adapter, not the reference spine):

- `run_game_with_faculty` returns a **list** of records, each embedding `effective_decisions`
  and an `open_levers` frozenset; requires `submissions_by_round` length **== num_rounds**
  (service pads future rounds and publishes only 1..t; sets normalized to lists for JSON).
- `customer_agents.focus_group` needs `Persona` **instances**, not dicts.
- advisor `context` must be exactly `{stated_warmth, eq, trust}`; the segment-level stated
  warmth is computed from `SAYDO_GAP` (the module's `stated_warmth` takes a Persona).
- `advisors.build_usage_record(team, round, advisor_ids, decision)` — ids, not stances.
- `defense.assemble_defense_packet(team, history, decisions_by_round, fg, advisor)` — team first.

These are also latent bugs in the reference `flexee_service.py` spine (its AI/defense wiring
was never exercised against the shipping modules); reported to Vikram. `make_flexee_game`
needs those fixes before the spine path runs, but the API layer (`GameService`) is correct.

To go live: copy the verified modules into `flexee_engine/vendor/` (list in
`flexee_engine/vendor/README.md`), restart — `engine_mode` flips to `"real"`. Then confirm
the config mapping in `flexee_engine/_real.py`. Nothing else changes.

### The spine + the persistence store (the intended integration)

The reference `flexee_service.py` is one `FlexeeGame` class — the **proven spine** whose
pure-runner assumption was verified against the reference engine across all 14 closes to
zero difference. Its persistence is a clean seam: `InMemoryStore` today, with the exact
method surface a database version needs.

That database version is **`flexee_engine/django_store.py` → `DjangoStore`** — a drop-in
replacement for `InMemoryStore` (same two methods + five write-through dict/list
attributes), backed by the Django models. So wiring the proven spine to the DB is
mechanical:

```python
from flexee_service import FlexeeGame          # vendored alongside the engine
from flexee_engine.django_store import DjangoStore
game = FlexeeGame(team_names, model_client=mc, store=DjangoStore(game_row))
```

`flexee_engine/spine.py::make_flexee_game(game_row)` does exactly this (config from the
persisted snapshot, shared model client injected) and is a no-op until the engine is
vendored. `DjangoStore` supports every `self.store.*` operation `FlexeeGame` performs.

`RoundResult.raw` stores the engine's record verbatim, so the store returns exactly what
the runner produced — unknown/future engine keys survive the round-trip.

**Two layers, one truth.** `games/services.py::GameService` is the HTTP-facing service the
seven screens call today; on the dev stub it re-derives history from submissions itself. It
and the `FlexeeGame` spine make the *same* underlying pure-runner call, so results match.
When the engine lands you can keep `GameService` (it routes through the seam automatically)
or drive `make_flexee_game()` directly — `DjangoStore` makes the latter mechanical.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                      # optional; sensible dev defaults without it
python manage.py migrate
python manage.py seed_demo                # faculty + 4 teams + students
python manage.py runserver
```

Then check `http://127.0.0.1:8000/api/health/`.

Demo credentials (from `seed_demo`): faculty `faculty` / `facultypass123`; students
`student_a`..`student_d` / `studentpass123`.

## Auth

Token auth (also session auth for the browsable API). Get a token:

```bash
curl -X POST localhost:8000/api/auth/login/ -H 'Content-Type: application/json' \
  -d '{"username":"faculty","password":"facultypass123"}'
# -> {"token": "...", "user": {...}}
```

Send it as `Authorization: Token <token>` on every `/api/...` call. Roles: **faculty**
(configure/steer, see everything) vs **student** (play one team, student-safe output only).

## API surface (maps to the screen contracts)

| Screen | Method & path | Who |
|---|---|---|
| Health / engine mode | `GET /api/health/` | anyone |
| Persona pool (id+name only) | `GET /api/reference/personas/` | auth |
| Advisor roster | `GET /api/reference/advisors/` | auth |
| Default config | `GET /api/reference/default-config/` | auth |
| A1 Faculty setup | `POST /api/games/` | faculty |
| List / detail | `GET /api/games/`, `GET /api/games/{id}/` | scoped |
| State summary | `GET /api/games/{id}/state/` | member |
| A2 Open levers | `GET /api/games/{id}/open-levers/?round=` | member |
| A2 Submit decision | `POST /api/games/{id}/submit/` | student(own)/faculty |
| A2 Close round | `POST /api/games/{id}/close-round/` | faculty |
| A2 Round result | `GET /api/games/{id}/results/?round=` | scoped |
| A3 Focus group | `POST /api/games/{id}/focus-group/` | student(own)/faculty |
| A4 Advisor panel | `POST /api/games/{id}/advisors/` | student(own)/faculty |
| A5 Scorecard | `GET /api/games/{id}/scorecard/` | scoped |
| A6 Board defense | `POST /api/games/{id}/defense/` | student(own)/faculty |
| A7 Faculty dashboard | `GET /api/games/{id}/dashboard/` | faculty |
| Roster: assign student | `POST /api/games/{id}/assign-student/` | faculty |

### Decision payload (submit)

Send only the **open** levers for the round; closed levers are rejected ("that lever isn't
open yet"). Validation mirrors the UI checks (allocation sums to budget, emphasis sums to 1,
positioning 0–100, price in band, promotion 0–1):

```json
{"round": 4, "levers": {"brand": 4, "perf": 3, "research": 3,
                         "emphasis": [0.2, 0.2, 0.2, 0.2, 0.2]}}
```

Students act for their own team automatically; faculty include `"team": "Team A"`.

## Data safety (enforced server-side)

- Focus-group responses never include segment or a raw score — only a warmth **band**.
- Students see only their own team's results and an anonymized best/avg/worst cohort.
- The faculty dashboard holds the de-anonymization view (a UI overlay; the cohort math is
  always anonymized per the docs).
- Board defense returns the deterministic grade and the prose grade as **two blocks** —
  never blended into one number.

## LLM voice layer

Personas, advisors, and the written-defense grade are driven by one shared model client
(`flexee_engine/model_client.py`, vendored unchanged from the handoff). Default provider is
`offline` (deterministic, no key, no network). Set in `.env`:

```
FLEXEE_LLM_PROVIDER=anthropic   # or openai
FLEXEE_LLM_MODEL=claude-3-5-sonnet-latest
ANTHROPIC_API_KEY=...
```

and `pip install anthropic` (or `openai`).

## Database

SQLite by default. Set the `POSTGRES_*` vars in `.env` to use Postgres (the schema is
storage-agnostic). `python manage.py migrate` either way.

## Contract alignment (cross-checked against the `ref_` bundle)

The dev stub and seam were aligned to the verified I/O contracts using Vikram's `ref_`
cross-check implementations and the authoritative authored files:

- **Real personas** — `flexee_engine/vendor/personas.py` (authoritative, hash-verified,
  `test_personas.py` green). The stub uses the real 28-persona pool; `idiosyncrasy` is the
  real float say-do offset.
- **Real advisor ids/names** — elena, ray, jax, anita (reads *stated* warmth, inheriting
  the say-do gap), bud, tom.
- **Warmth scale [0,1]** centered at 0.5 with the verified per-segment say-do gap
  (LEG +0.20, FIT 0, FUN −0.20, GENZ −0.15, MAIN +0.05); the app layer converts the raw
  float to a display band.
- **`build_report`** returns a *list* of `{round, teams:{...}}` (not the earlier
  `{rounds:[...]}`); scorecard views updated to match.
- **`build_defense`** grades on emphasis·(revealed−stated) reconciliation, outcome,
  engagement, coherence; `assemble_defense_packet` returns `focus_group_evidence /
  decisions / final_scorecard` for the grader.
- **`_real.py`** binds engine helpers defensively (name/arg drift on `open_levers_for*`,
  `build_usage_record`, `assemble_defense_packet`) so one renamed helper can't disable the
  whole real path.

## Locked product decisions (Vikram, 2026-08-07) — implemented

1. **Compound bets = narrative in v1.** No discrete bet levers; screens/defense claim only
   the real carries (equity, deal-conditioning, crisis buffering, persona relationships).
   The backend exposes only the five levers — nothing to change.
2. **Round lifecycle:** locks on close; faculty can reopen; missing submission = baseline
   (no extra penalty). → `Game.locked_rounds`; `submit` rejects a locked round;
   `POST /api/games/{id}/reopen-round/` (faculty).
3. **Engagement:** faculty-set per-round **cap** (`engagement_cap_per_round`, default 3) +
   **diminishing returns** on the grade. → cap enforced in `focus_group`/`consult_advisors`;
   `engagement()` applies a saturating transform (`1 + f + f² + …`) in the **app layer**,
   before `build_defense`, so the locked module is never edited. Raw counts still shown on
   the faculty dashboard as `engagement_counts`.
4. **Budget = fixed per round.** `brand+perf+research = round_budget` (validator stands).

### Additional locked decisions (2026-08-07, grounded in a real playthrough)

5. **Crisis is LIVE by derivation.** At the crisis round close, `GameService` derives per-team
   `crisis_inputs` from the pre-crisis state — `exposure = 1 − positioning/100` (heritage lean),
   `preempt = TRUST/100` (preparedness) — so the R9 hit lands on the heritage/high-caffeine team
   and the wellness-builder rides it out, with no engine change. Faculty may author `crisis_inputs`
   at setup to override the derivation. Damage: `damage_base·exposure·(1−eq/100)·(1−preempt)·(1−response)`.
6. **Faculty scorecard weights honored in the app layer.** `overall = Σ weightᵢ·dimensionᵢ` over the
   four normalized perspectives the record already exposes (engine default = 0.25 each, so default
   weights reproduce the engine exactly — no golden change). Applied in `scorecard()` and re-weighted
   into the board-defense outcome term for consistency.
7. **Reference spine retired.** `GameService` is the canonical orchestration layer;
   `flexee_service.py`/`make_flexee_game` are illustrative only (their AI/defense wiring has
   signature bugs vs the shipping modules and is not used by the app).

## Notes / roadmap

- The dev stub is **not calibrated** — its numbers are plausible placeholders to exercise
  the plumbing. Real numbers come from the verified engine (drop into `vendor/`).
- **Confirmed seam:** `run_game_with_faculty` returns `(history, built)`; `_real.run_history`
  unwraps it and embeds `built[i]` into each record's `effective_decisions` (still confirm
  `assemble_defense_packet`'s name in the shipping `defense.py`).
- No automated tests ship in this snapshot; add per-app unit tests as the frontend
  firms up the exact response shapes it needs.
