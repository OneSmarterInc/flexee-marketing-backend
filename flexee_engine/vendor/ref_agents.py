"""In-repo binding for the market-research reference (was `ref_agents` in the bundle).
Re-exports the single say-do bias source from the shipped customer-agent layer and
provides the SEGMENT-level stated warmth the reference expects (customer_agents.stated_warmth
is persona-level)."""
from customer_agents import SAYDO_GAP, GAP_GAIN, revealed_warmth  # noqa: F401


def stated_warmth(revealed, seg, idiosyncrasy=0.0):
    return max(0.0, min(1.0, float(revealed) + GAP_GAIN * SAYDO_GAP[seg] + float(idiosyncrasy)))
