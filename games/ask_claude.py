"""Ask Claude for faculty.

One question runs during the request. Claude reads the facilitator guide and the page it was asked
from, looks up what it needs through `AskTools` (read-only, this game only) and answers. The answer
goes through the figure check before anyone sees it, and the exchange is logged for 90 days.

Built in from the sibling products' live fixes: the answer is every turn's text without the "I'll
look at …" lines; a reply that only announces lookups is sent back once; a lookup's empty input
goes back as {}; and earlier answers in a conversation are treated as already checked.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from django.conf import settings

from . import ai_use, models
from .ask_text import announces_lookups, compose, figure_check
from .ask_tools import AskTools
from .facilitator_guide import FACILITATOR_GUIDE

log = logging.getLogger("flexee.ask")

ASK_RETENTION_DAYS = 90
MAX_QUESTION_CHARS = 1000
HISTORY_TURNS = 4

REFUSALS = {
    "unavailable": "Ask Claude isn't set up on this site yet.",
    "off_switch": "Ask Claude is off for this simulation. Turn it on in Settings.",
    "off_ceiling": "Ask Claude has reached this month's limit for the whole site. It will be back on the 1st.",
    "length": f"Ask a question of up to {MAX_QUESTION_CHARS:,} characters.",
    "allowance": "This simulation has used all its questions for this month. They start again on the 1st.",
    "unreachable": "Claude couldn't be reached just now. Please try again in a minute.",
    "all_dropped": ("Every sentence of this answer quoted a figure the lookups did not return, so none of it is "
                    "shown. Try asking in a different way."),
}

# Earlier turns go back as plain text, without their lookups.
EARLIER = ("Your earlier answers in this conversation were built from lookups that are not repeated here, and every "
           "figure in them was checked against those lookups before the faculty member saw it. Treat them as correct; "
           "look things up again only for what they did not cover, and don't comment on how they were made.")


class AskRefused(Exception):
    """A refusal the panel shows as written (400, or 503 when Claude can't be reached)."""

    def __init__(self, message: str, unreachable: bool = False):
        super().__init__(message)
        self.unreachable = unreachable


@dataclass
class AskSettings:
    api_key: str | None
    model: str
    thinking: str
    max_tokens: int
    questions_per_month: int
    timeout: float = 55.0
    max_lookup_rounds: int = 4
    input_usd: float | None = None
    output_usd: float | None = None


def _opt_float(name):
    raw = os.environ.get(name, "").strip()
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v >= 0 else None


def ask_settings() -> AskSettings:
    try:
        max_tokens = int(os.environ.get("FLEXEE_ASK_MAX_TOKENS", "4000"))
    except ValueError:
        max_tokens = 4000
    return AskSettings(
        api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        model=getattr(settings, "FLEXEE_ASK_MODEL", "claude-sonnet-5-5"),
        thinking=os.environ.get("FLEXEE_ASK_THINKING", "").strip(),
        max_tokens=max_tokens,
        questions_per_month=int(getattr(settings, "FLEXEE_ASK_QUESTIONS_PER_MONTH", 100)),
        input_usd=_opt_float("FLEXEE_ASK_INPUT_USD_PER_MTOK"),
        output_usd=_opt_float("FLEXEE_ASK_OUTPUT_USD_PER_MTOK"),
    )


def state(game: models.Game, now: datetime | None = None) -> str:
    """on, or why not: unavailable (no key), off_switch, off_ceiling."""
    if not ask_settings().api_key:
        return ai_use.UNAVAILABLE
    return ai_use.state(game, now, needs_key=False)


def questions_left(game: models.Game, now: datetime | None = None) -> int:
    used = models.AskQuestion.objects.filter(
        game=game, status="answered", created_at__gte=ai_use.month_start(now)).count()
    return max(0, ask_settings().questions_per_month - used)


def send(settings_: AskSettings, system: list, tools: list, messages: list) -> dict:
    """One call to Claude. Replaced in tests with a stub."""
    import anthropic
    client = anthropic.Anthropic(api_key=settings_.api_key, timeout=settings_.timeout)
    kwargs = dict(model=settings_.model, max_tokens=settings_.max_tokens,
                  system=system, tools=tools, messages=messages)
    if settings_.thinking:
        kwargs["thinking"] = {"type": settings_.thinking}
    return client.messages.create(**kwargs).model_dump()


def replayable(content: list) -> list:
    """Claude's turn as it must be sent back: a lookup's input always goes back as an object."""
    out = []
    for block in content:
        if block.get("type") == "tool_use":
            inp = block.get("input")
            block = {**block, "input": inp if isinstance(inp, dict) else {}}
        out.append(block)
    return out


def _history(user, game, conversation_id) -> list:
    if not conversation_id:
        return []
    rows = list(models.AskQuestion.objects.filter(
        game=game, user=user, conversation_id=conversation_id, status="answered"
    ).order_by("-created_at")[:HISTORY_TURNS])
    return rows[::-1]


def _page(game, context: dict) -> str:
    details = "; ".join(f"{k}: {v}" for k, v in (context.get("details") or {}).items())
    return (f'The faculty member is on the "{context.get("label", "")}" page of the simulation "{game.name}".'
            + (f" On screen: {details}." if details else ""))


def ask(*, user, game: models.Game, context: dict, question: str, conversation_id: str,
        now: datetime | None = None, send_fn=None) -> dict:
    cfg = ask_settings()
    question = (question or "").strip()
    st = state(game, now)
    if st != ai_use.ON:
        raise AskRefused(REFUSALS[st])
    if not question or len(question) > MAX_QUESTION_CHARS:
        raise AskRefused(REFUSALS["length"])
    left_before = questions_left(game, now)
    if left_before == 0:
        raise AskRefused(REFUSALS["allowance"])

    history = _history(user, game, conversation_id)
    tools = AskTools(game, now)
    messages: list = []
    for turn in history:
        messages += [{"role": "user", "content": turn.question},
                     {"role": "assistant", "content": turn.answer or ""}]
    messages.append({"role": "user", "content": question})
    page = _page(game, context) + (f" {EARLIER}" if history else "")
    system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": page}]
    sources = [FACILITATOR_GUIDE, question, str(context)]
    lookups: list = []
    parts: list = []
    tokens_in = tokens_out = 0
    answered = nudged = False
    call = send_fn or send

    try:
        for rnd in range(cfg.max_lookup_rounds + 1):
            response = call(cfg, system, tools.definitions(), messages)
            usage = response.get("usage") or {}
            tokens_in += (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) \
                + (usage.get("cache_creation_input_tokens") or 0)
            tokens_out += usage.get("output_tokens") or 0
            content = replayable(list(response.get("content") or []))
            stop = str(response.get("stop_reason") or "")
            uses = [b for b in content if b.get("type") == "tool_use"]
            text = "\n\n".join(str(b.get("text") or "") for b in content if b.get("type") == "text").strip()
            # The answer is every turn's text, less the "I'll look at …" lines.
            if text and not announces_lookups(text):
                parts.append(text)
            if stop == "pause_turn":          # a long turn the API paused: send it back and Claude carries on
                messages.append({"role": "assistant", "content": content})
                continue
            if not uses or stop == "max_tokens":
                # A reply that only says what it will look up, before any lookup, is sent back once.
                if not lookups and not nudged and rnd < cfg.max_lookup_rounds and announces_lookups(text):
                    nudged = True
                    messages += [{"role": "assistant", "content": content},
                                 {"role": "user", "content": "Go ahead: use the lookups now, then answer the question."}]
                    continue
                answered = bool(parts)
                break
            messages.append({"role": "assistant", "content": content})
            results = []
            for use in uses:
                result = tools.run(str(use.get("name")), use.get("input") or {})
                sources.append(str(result))
                lookups.append({"name": str(use.get("name")), "summary": tools.summary(str(use.get("name")), result)})
                results.append({"type": "tool_result", "tool_use_id": use.get("id"), "content": _json(result)})
            messages.append({"role": "user", "content": results})
    except Exception:
        log.exception("[ask-claude] request failed")

    if answered:
        check = figure_check(compose(parts), sources, [])
        if check.answer == "":
            check.answer = REFUSALS["all_dropped"]
    else:
        check = None
    qid = _record(user=user, game=game, context=context, conversation_id=conversation_id, question=question,
                  answer=check.answer if check else None, lookups=lookups,
                  dropped=check.dropped if check else [], checked=check.checked if check else 0,
                  tokens_in=tokens_in, tokens_out=tokens_out, cfg=cfg)
    if not answered:
        raise AskRefused(REFUSALS["unreachable"], unreachable=True)
    try:
        left = questions_left(game, now)
    except Exception:       # the answer is saved and counted by now: a database blip is not an error
        left = max(0, left_before - 1)
    return {"id": qid, "answer": check.answer, "dropped": check.dropped, "lookups": lookups,
            "checked": check.checked, "questions_left": left, "tables": tools.views}


def _json(obj) -> str:
    return json.dumps(obj, default=str)


def _record(*, user, game, context, conversation_id, question, answer, lookups, dropped, checked,
            tokens_in, tokens_out, cfg: AskSettings) -> str:
    """Never raises: a logging failure must not lose the answer. Writes the question and the AI call."""
    qid = uuid.uuid4().hex
    cost = ai_use.call_cost(tokens_in, tokens_out, cfg.model, cfg.input_usd, cfg.output_usd)
    try:
        models.AskQuestion.objects.create(
            game=game, user=user if getattr(user, "pk", None) else None,
            conversation_id=(conversation_id or "")[:64], page=str(context.get("page", ""))[:64],
            context=context, question=question, answer=answer or "", lookups=lookups, dropped=dropped,
            figures_checked=checked, status="answered" if answer is not None else "failed",
            model=cfg.model, input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=cost)
        # Rows are kept 90 days.
        models.AskQuestion.objects.filter(
            created_at__lt=datetime.now(timezone.utc) - timedelta(days=ASK_RETENTION_DAYS)).delete()
    except Exception:
        log.exception("[ask-claude] could not record the question")
    ai_use.record_call(game, "ask_claude", outcome="error" if answer is None else "ok",
                       input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=cost,
                       sentences_dropped=len(dropped))
    return qid


SYSTEM = f"""You are Ask Claude, the helper on the faculty pages of Flexee Marketing Management, a brand-management simulation. Each team runs the marketing of one brand across 14 rounds, and levers open on a schedule so the team's decisions grow over the game. Each round closes at 23:59 in the game's time zone on its due date, or earlier when a faculty member presses Advance round; then the next round opens.

How to answer:
- Answer the question in a few short, plain sentences. No headings, no bold, and no bullet lists unless the faculty member asks for a list.
- Use the lookups straight away, without first saying what you will look up; the text you write is the answer the faculty member reads.
- Rounds close at 23:59 in the game's time zone; say the zone whenever you give a time. Say a round "closes" or "closed": results appear once a round closes.
- Use the lookups for anything about this game: where it stands, which teams have submitted, when rounds closed, the teams' stored results and scores, what each team decided and how its forecasts did. Use the guide for how the simulation works.
- For results, read them with get_results; when figures matter to the answer, call show_table once so the faculty member sees them beside it. The table is drawn below your text, so if you mention it, call it "the table below". For forecasts, use get_forecast_accuracy.
- Quote figures exactly as the lookups and the guide give them; don't round, add up or work out new figures. Describe a change in words rather than computing it. Never guess a date, a count, a status or any other figure: if a lookup or the guide didn't give it to you, don't state it. Any sentence with a figure that no lookup or the guide gave you is removed before the faculty member sees it.
- You can only read. If the faculty member wants something done (advance or extend a round, change the schedule, move a student), say which page does it.
- This is for faculty only. Never write anything meant to be passed on to students, and don't suggest what a team should decide. Describe what happened; you may say how teams compare on the scores and the rank the simulation itself shows, but don't grade them beyond that.
- Rounds 1 to 3 are a listening period; teams decide from the first decision round the status lookup gives.
- A reference firm is a scripted firm in a practice game, not a student team.
- If the guide and the lookups don't answer the question, say so plainly.

The facilitator guide:
{FACILITATOR_GUIDE}"""
