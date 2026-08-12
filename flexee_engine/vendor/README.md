# vendor/ — drop the verified engine modules here

Copy the **locked, verified** engine modules into this folder (they are on `sys.path`):

```
engine.py  equity.py  promotion.py  crisis.py  simulation.py  scorecard.py
game.py  game_faculty.py  faculty.py  calibration.py  flexee_calibrated_config.py
personas.py  customer_agents.py  advisors.py  live_voice.py  advisor_voice.py
report.py  defense.py  defense_grader.py
```

On the next process start, `flexee_engine.api.ENGINE_MODE` becomes `"real"` and the dev
stub is bypassed. Then review `flexee_engine/_real.py` to confirm the JSON-config →
dataclass mapping matches the real field names. Nothing else in the Django app changes.
