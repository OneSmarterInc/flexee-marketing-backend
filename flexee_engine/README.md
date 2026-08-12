# flexee_engine — the engine seam

The Flexee simulation engine is **built, verified, and locked** by the product team. Per
the handoff docs, it is *not* part of the developer's layer — the Django app wraps it.

This package is the single seam between Django and that engine. Everything the backend
needs from the engine goes through **`flexee_engine/api.py`**. Nothing else in the Django
code imports engine internals directly, so swapping the stub for the real engine is a
one-folder change.

## How it resolves the engine

`api.py` chooses an implementation at import time:

1. **Real engine (preferred).** If the verified modules are importable, it uses
   `flexee_engine/_real.py`, which adapts our JSON-config contract to the real engine's
   dataclasses and calls `run_game_with_faculty`, `customer_agents.focus_group`,
   `advisors.advise`, `report.build_report`, `defense.*`, etc. — exactly as the reference
   `flexee_service.py` in the handoff does.
2. **Dev stub (fallback).** Otherwise it uses `flexee_engine/_stub.py`: a deterministic,
   **NOT calibrated** simulation that returns outputs in the *exact shapes* the UI data
   contracts specify, so the whole API runs end-to-end today with no engine and no LLM key.

`flexee_engine.api.ENGINE_MODE` is `"real"` or `"stub"` — the `/api/health/` endpoint and
logs report it, so you always know which is active.

## Dropping in the real engine

Copy the verified modules into **`flexee_engine/vendor/`**:

```
flexee_engine/vendor/
    engine.py  equity.py  promotion.py  crisis.py  simulation.py  scorecard.py
    game.py  game_faculty.py  faculty.py  calibration.py  flexee_calibrated_config.py
    personas.py  customer_agents.py  advisors.py  live_voice.py  advisor_voice.py
    report.py  defense.py  defense_grader.py
```

`vendor/` is on `sys.path`, so those top-level imports (`from game_faculty import ...`)
resolve. On the next process start, `ENGINE_MODE` flips to `"real"` automatically. Then
open `_real.py` and confirm the config mapping matches the real dataclass field names
(the docstrings in the reference `flexee_service.py` flag the couple of helper names to
double-check against the modules' `test_step*.py`).

## The AI voice layer

`api.py` builds one shared model client from `flexee_engine/model_client.py` (the file
provided in the handoff, vendored unchanged) using `settings.FLEXEE_LLM_PROVIDER`. The
default `offline` provider needs no key and no network. Set the provider to `anthropic`
or `openai` (with the matching key) to get live persona/advisor/defense prose.
