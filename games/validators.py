"""Student decision validation (mirrors the UI-side checks in the contracts doc).

A submission carries only the fields for levers OPEN this round. Closed levers must be
omitted — submitting one is a gating error ("that lever isn't open yet"). Open-lever
fields are range/sum checked so a student gets immediate feedback before the runner.
"""

from rest_framework.exceptions import ValidationError

# lever name -> the decision fields it controls
LEVER_FIELDS = {
    "allocation": ("brand", "perf", "research"),
    "emphasis": ("emphasis",),
    "positioning": ("positioning",),
    "promotion": ("promotion",),
    "price": ("price",),
    # Group B enrichment (app-layer levers threaded into the engine via the seam)
    "channel": ("channel",),
    "message": ("message",),
    "line_extension": ("line_extension",),
}

_ALL_FIELDS = {f for fields in LEVER_FIELDS.values() for f in fields}

CHANNELS = ("RETAIL", "DTC", "SOCIAL")
MESSAGES = ("NEUTRAL", "PERFORMANCE", "HERITAGE", "CLEAN_ENERGY", "COMMUNITY")


def _num(value, field):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError({field: "must be a number"})


def validate_decision(levers: dict, open_levers: list, config: dict) -> dict:
    """Return a cleaned dict of only the open-lever fields, or raise ValidationError."""
    levers = dict(levers or {})
    open_levers = set(open_levers or [])
    open_fields = {f for lever in open_levers for f in LEVER_FIELDS[lever]}

    # Gating: no closed-lever field may appear.
    for field in list(levers.keys()):
        if field in _ALL_FIELDS and field not in open_fields:
            raise ValidationError(
                {field: "that lever isn't open yet"}
            )

    cleaned: dict = {}

    if "allocation" in open_levers:
        budget = float(config.get("round_budget", 10.0))
        brand = _num(levers.get("brand", 0), "brand")
        perf = _num(levers.get("perf", 0), "perf")
        research = _num(levers.get("research", 0), "research")
        for name, v in (("brand", brand), ("perf", perf), ("research", research)):
            if v < 0:
                raise ValidationError({name: "must be >= 0"})
        if abs((brand + perf + research) - budget) > 1e-6:
            raise ValidationError(
                {"allocation": f"brand + perf + research must equal the budget ({budget})"}
            )
        cleaned.update(brand=brand, perf=perf, research=research)

    if "emphasis" in open_levers:
        emphasis = levers.get("emphasis")
        if not isinstance(emphasis, (list, tuple)) or len(emphasis) != 5:
            raise ValidationError(
                {"emphasis": "must be 5 fractions over LEG/FIT/FUN/GENZ/MAIN"}
            )
        emphasis = [_num(e, "emphasis") for e in emphasis]
        if any(e < 0 for e in emphasis):
            raise ValidationError({"emphasis": "fractions must be >= 0"})
        if abs(sum(emphasis) - 1.0) > 1e-6:
            raise ValidationError({"emphasis": "fractions must sum to 1"})
        cleaned["emphasis"] = emphasis

    if "positioning" in open_levers:
        pos = _num(levers.get("positioning"), "positioning")
        if not (0.0 <= pos <= 100.0):
            raise ValidationError({"positioning": "must be in [0, 100]"})
        cleaned["positioning"] = pos

    if "promotion" in open_levers:
        promo = _num(levers.get("promotion"), "promotion")
        if not (0.0 <= promo <= 1.0):
            raise ValidationError({"promotion": "must be in [0, 1]"})
        cleaned["promotion"] = promo

    if "price" in open_levers:
        band = config.get("price_band", [1.10, 1.90])
        lo, hi = float(band[0]), float(band[1])
        price = _num(levers.get("price"), "price")
        if not (lo <= price <= hi):
            raise ValidationError({"price": f"must be in [{lo}, {hi}]"})
        cleaned["price"] = price

    # --- Group B enrichment levers ---
    if "channel" in open_levers:
        ch = levers.get("channel")
        if not isinstance(ch, dict) or set(ch) != set(CHANNELS):
            raise ValidationError({"channel": f"must set weights for {', '.join(CHANNELS)}"})
        w = {c: _num(ch.get(c), "channel") for c in CHANNELS}
        if any(v < 0 for v in w.values()):
            raise ValidationError({"channel": "weights must be >= 0"})
        if abs(sum(w.values()) - 1.0) > 1e-6:
            raise ValidationError({"channel": "weights must sum to 1"})
        cleaned["channel"] = w

    if "message" in open_levers:
        msg = levers.get("message")
        if msg not in MESSAGES:
            raise ValidationError({"message": f"must be one of {', '.join(MESSAGES)}"})
        cleaned["message"] = msg

    if "line_extension" in open_levers:
        le = levers.get("line_extension")
        if not isinstance(le, dict):
            raise ValidationError({"line_extension": "must be an object"})
        pos = _num(le.get("positioning", 90.0), "line_extension.positioning")
        if not (0.0 <= pos <= 100.0):
            raise ValidationError({"line_extension": "positioning must be in [0, 100]"})
        cleaned["line_extension"] = {"launch": bool(le.get("launch", False)), "positioning": pos}

    return cleaned
