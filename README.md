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
deterministic **dev stub** in the same contract shapes. `GET /meridian/api/health/` reports which is
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
python manage.py seed_demo                # optional: a demo faculty + 4 teams + students
python manage.py runserver
```

Then check `http://127.0.0.1:8000/meridian/api/health/`.

Create faculty and student accounts from the sign-up page. Admins can't be self-created; set
one from the server:

```bash
python manage.py set_admin --email <email>     # asks for the password
```

Admins sign in with that email and password. Run it again for the same email to change the password.

## Auth

Token auth (also session auth for the browsable API). Get a token:

```bash
curl -X POST localhost:8000/meridian/api/auth/login/ -H 'Content-Type: application/json' \
  -d '{"username":"<username>","password":"<password>"}'
# -> {"token": "...", "user": {...}}
```

Send it as `Authorization: Token <token>` on every `/meridian/api/...` call. Roles: **faculty**
(configure/steer, see everything) vs **student** (play one team, student-safe output only).

## API surface (maps to the screen contracts)

| Screen | Method & path | Who |
|---|---|---|
| Health / engine mode | `GET /meridian/api/health/` | anyone |
| Persona pool (id+name only) | `GET /meridian/api/reference/personas/` | auth |
| Advisor roster | `GET /meridian/api/reference/advisors/` | auth |
| Default config | `GET /meridian/api/reference/default-config/` | auth |
| A1 Faculty setup | `POST /meridian/api/games/` | faculty |
| List / detail | `GET /meridian/api/games/`, `GET /meridian/api/games/{id}/` | scoped |
| State summary | `GET /meridian/api/games/{id}/state/` | member |
| A2 Open levers | `GET /meridian/api/games/{id}/open-levers/?round=` | member |
| A2 Submit decision | `POST /meridian/api/games/{id}/submit/` | student(own)/faculty |
| A2 Close round | `POST /meridian/api/games/{id}/close-round/` | faculty |
| A2 Round result | `GET /meridian/api/games/{id}/results/?round=` | scoped |
| A3 Focus group | `POST /meridian/api/games/{id}/focus-group/` | student(own)/faculty |
| A4 Advisor panel | `POST /meridian/api/games/{id}/advisors/` | student(own)/faculty |
| A5 Scorecard | `GET /meridian/api/games/{id}/scorecard/` | scoped |
| A6 Board defense | `POST /meridian/api/games/{id}/defense/` | student(own)/faculty |
| A7 Faculty dashboard | `GET /meridian/api/games/{id}/dashboard/` | faculty |
| Roster: assign student | `POST /meridian/api/games/{id}/assign-student/` | faculty |

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
   `POST /meridian/api/games/{id}/reopen-round/` (faculty).
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

## Deadlines, the scheduler and reminders

- Creating a game needs the **time zone** the class meets in (a real IANA name, no default). Every
  deadline is on the game's own calendar.
- A round closes at **23:59 game time on its due date**, or sooner when a faculty member presses
  Advance. A change after the deadline is refused ("Round N has closed, so this change was not
  saved"). The last round is never closed automatically; a faculty member closes it. Practice
  games have no deadlines.
- **Deploying for students opens the current round now**: the schedule is re-timed from today.
- A team that has not submitted when a round closes plays the cohort average (unchanged).
- Every close path (Advance, the sweep, a page load after the deadline) takes one atomic claim
  (`Game.close_claimed_at`, stale after 15 minutes), so two closes can never run at once. Each
  close is recorded in `RoundCloseLog` (who closed it, how long it took, how many teams had
  submitted); that history survives the game being deleted.
- **Auto-advance** has three entry points, all sharing the claim: `python manage.py run_scheduler`
  (a loop, `--interval 300`; `--once` for one sweep, e.g. from cron), an external cron calling
  `GET|POST /meridian/api/cron/round-tick/` with `Authorization: Bearer $CRON_SECRET`, and a lazy
  check on every `state`/`overview` page load. If only gunicorn runs (no scheduler process), the
  lazy path still enforces deadlines when anyone opens a page. The sweep writes a heartbeat the
  reporting connector reads (`scheduler_ok` = ran in the last 10 minutes).
- **Day-before reminder email**: about a day before a round closes (half a round when rounds are
  shorter than two days), once per round, only to students whose team has not submitted. Skipped:
  inactive accounts, duplicate addresses, `test-N@mailinator.com`, reference firms, practice
  games and rounds with nothing open. `REMINDER_EMAILS_ENABLED=false` turns it off;
  `EMAIL_DRY_RUN=true` logs instead of sending. With the console/dummy mail backend nothing is
  sent and the round is not marked as reminded.
- **Password reset** by emailed six-digit code: `POST /auth/password-reset/{request,verify,confirm}/`.
  The answer never says whether an email has an account; a code lasts 15 minutes, can be asked for
  again after 60 seconds, dies after 5 wrong tries, is stored only as a hash, and resetting signs
  out every session. Every wrong-code case gives one message, "Invalid or expired code".

## Practice games

`POST /games/:id/practice/` makes the caller a private practice copy of a live game: one firm of
their own ("Your practice firm") against three scripted reference firms, **Heritage Defender**,
**Performance Chaser** and **Wellness Pivoter**. Asking again returns the same copy;
`POST /games/:id/practice/reset/` starts over. The three paths are the engine's own calibration
archetypes (HERITAGE, PERF, PIVOT; a test pins them to `calibration.py`) played the same way every
round, fitted to the game's budget and price band, and written inside each close before the engine
computes, so no reference firm is ever a missing submission. A practice game starts at the takeover
round, has no deadlines or students, and closes rounds only on Advance. It stays out of billing,
the scheduler, reminders and the reporting connector. The faculty member plays their own firm
through the normal student screens.

## AI use and Ask Claude

- Every Claude call from focus groups, advisors and the written-defense grade is measured (tokens
  from the API response) and logged once per use in `AiCall` with its cost. Practice calls are
  flagged and count toward spend but not toward reports.
- One **monthly site ceiling** (`FLEXEE_AI_MONTHLY_CEILING_USD`, default 50, UTC calendar month)
  and one **switch per game** (`Game.ai_enabled`, `PATCH /games/:id/ai/`). Either one off means: no
  model client reaches the engine (focus groups and advisors use the engine's own templated
  voice), the written part of a defense is not graded (the decision grade still is), and Ask
  Claude refuses.
- **Ask Claude** (`GET|POST /games/:id/ask/`, faculty only): Claude reads this one game through
  read-only lookups (status, round close history, stored results, team detail, decisions, forecast
  accuracy, engagement) and `games/facilitator_guide.py`, never gets people's names, and cannot
  change anything. Every figure in an answer is checked against what the lookups returned; a
  sentence with a figure no lookup returned is dropped and the panel says so. 100 questions per
  game per month (`FLEXEE_ASK_QUESTIONS_PER_MONTH`); questions are kept 90 days; a failed question
  is kept and does not count. Model: `FLEXEE_ASK_MODEL` (default `claude-sonnet-5-5`). It needs
  `ANTHROPIC_API_KEY`, and one question can take up to about a minute, so run gunicorn with
  `--timeout 120` or more.

## Reporting connector (Flexee family reporting contract v1)

A read-only MCP endpoint so an administrator can connect Claude to operational reporting.

- Address: `{PUBLIC_API_URL}/meridian/api/mcp/reporting` (shown in Admin > Reporting). Discovery
  documents are served at the **host root**: `/.well-known/oauth-protected-resource[/meridian/api/mcp/reporting]`
  and `/.well-known/oauth-authorization-server[/meridian/api/oauth/reporting]`, so the host must route
  `/.well-known/` to Django. Set `PUBLIC_API_URL` (the API host, no path) and `FRONTEND_URL`.
- Auth: OAuth 2.0 authorization code with S256 PKCE, for Claude's Client ID Metadata Document
  (read only from claude.ai or claude.com) or Dynamic Client Registration. Redirects are allowed
  only on claude.ai, claude.com and loopback (any port). Only an **active administrator** can
  approve, on the web app's `/oauth/reporting/authorize` page, and a connection stops the moment its
  administrator stops being one. Codes are single-use for 10 minutes, access tokens last 60
  minutes, refresh tokens rotate and last 30 days; everything is stored as a SHA-256 hash.
- Six read-only tools, every row starting with `sim` (`marketing_management`): `report_courses`,
  `report_quarters`, `report_closes`, `report_requests` (always empty), `report_ai_use`,
  `report_health`. A course is a game and a quarter is a round (the rounds before the takeover round
  are `warm_up`). Practice games, test participants, names, emails and results are never included.
- 120 calls a minute per connection (per worker). Each call is logged (tool, status, rows,
  milliseconds; never the rows) and shown in Admin > Reporting, where a connection can be revoked.

## Deployment checklist for the above

```
python manage.py migrate
# one of: a scheduler process, an external cron, or neither (the lazy path still runs)
python manage.py run_scheduler            # or: --once from cron every 5 minutes
gunicorn config.wsgi --timeout 120 ...    # Ask Claude can take about a minute
```

Environment: `PUBLIC_API_URL`, `FRONTEND_URL`, `CRON_SECRET` (only for the cron endpoint),
`ANTHROPIC_API_KEY`, `FLEXEE_ASK_MODEL`, `FLEXEE_AI_MONTHLY_CEILING_USD`, `GIT_COMMIT` (shown by
`report_health`), `REMINDER_EMAILS_ENABLED`, `EMAIL_DRY_RUN`, and a real mail backend
(`MAIL_BACKEND`) for reminders and password reset. See `.env.example`.

## Tests

`python manage.py test` (Django, API and services) and `python -m pytest -q` (engine integrity).

## Notes / roadmap

- The dev stub is **not calibrated** — its numbers are plausible placeholders to exercise
  the plumbing. Real numbers come from the verified engine (drop into `vendor/`).
- **Confirmed seam:** `run_game_with_faculty` returns `(history, built)`; `_real.run_history`
  unwraps it and embeds `built[i]` into each record's `effective_decisions` (still confirm
  `assemble_defense_packet`'s name in the shipping `defense.py`).
