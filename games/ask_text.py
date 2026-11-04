"""The text rules Ask Claude's answers go through.

The figure check runs in code after every answer: each figure in the answer must appear in what
Claude was given (its lookups, the facilitator guide, the page and the question). A sentence
quoting a figure that appears in none of them is dropped, the rest is shown, and the panel says so.

Figures are compared as numbers, so "$2,800,000", "2800000" and "2,800,000" match, as do "5%"
and "5". A date's day and year are figures too, which is why the lookups hand dates over already
written out.

`compose` turns every turn's text into one answer: Claude narrates its steps in the turns before
the one that answers ("I'll read the latest closed round…"), and only the answer should reach
the faculty member.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# A minus sign is part of the figure: "-$50,000" is not "$50,000".
NUMBER = re.compile(r"(?<![\w.])[-−]?\$?\d[\d,]*(?:\.\d+)?%?")
# A sentence can end inside a closing quotation mark: 'asked: "… last week." There were …' is two.
SENTENCE_BREAK = re.compile(r"""(?:(?<=[.!?])|(?<=[.!?]["”’']))\s+(?=[A-Z0-9"“(])""")
QUOTED = re.compile(r'"[^"]*"|“[^”]*”')


def normalise_figure(figure: str) -> str:
    negative = bool(re.match(r"^[-−]", figure))
    n = re.sub(r"^[-−]", "", figure)
    n = re.sub(r",$", "", n)
    n = re.sub(r"[$,%]", "", n)
    if "." in n:
        n = re.sub(r"0+$", "", n)
        n = re.sub(r"\.$", "", n)
    n = re.sub(r"^0+", "", n)
    if n == "":
        return "0"
    return f"-{n}" if negative else n


def figures(sources: list[str]) -> set[str]:
    out: set[str] = set()
    for source in sources:
        for m in NUMBER.finditer(source):
            out.add(normalise_figure(m.group(0)))
    return out


@dataclass
class FigureCheckResult:
    answer: str
    dropped: list = field(default_factory=list)      # [{"sentence", "figures"}]
    checked: int = 0


def figure_check(answer: str, sources: list[str], quotable: list[str] | None = None) -> FigureCheckResult:
    known, quoted = figures(sources), figures(quotable or [])
    dropped: list[dict] = []
    checked = 0
    paragraphs: list[str] = []
    for paragraph in re.split(r"\n{2,}", answer.strip()):
        kept: list[str] = []
        for sentence in SENTENCE_BREAK.split(paragraph):
            spans = [(m.start(), m.end()) for m in QUOTED.finditer(sentence)]
            unknown: list[str] = []
            for m in NUMBER.finditer(sentence):
                checked += 1
                key = normalise_figure(m.group(0))
                in_quote = any(a <= m.start() < b for a, b in spans)
                if key not in known and not (in_quote and key in quoted):
                    unknown.append(re.sub(r",$", "", m.group(0)))
            if not unknown:
                kept.append(sentence)
            else:
                dropped.append({"sentence": sentence.strip(), "figures": unknown})
        if kept:
            paragraphs.append(" ".join(kept))
    return FigureCheckResult("\n\n".join(paragraphs), dropped, checked)


# ---------------------------------------------------------------------------- compose
_VERBS = r"(?:show|look|check|pull|try|use|start|get|find|compare|fetch|run|call|open|see|gather|draw|put|bring|read|include|list)"
_STARTS = re.compile(
    r"^\s*(?:(?:First|Now|Next|Then),?\s+)?(?:I'll|I will|I'm going to|Let me|I need to|I'd better)\s+"
    rf"(?:also\s+|now\s+|first\s+)?{_VERBS}\b", re.IGNORECASE)
_REPORTS = re.compile(
    rf",?\s+so\s+(?:I'll|I will|let me)\s+{_VERBS}\b|\b(?:came back empty|returned nothing)\b", re.IGNORECASE)
_NAMES = re.compile(r"\b(?:Round|R|Team)\s?\d{1,2}\b")


def _quotes_figure(s: str) -> bool:
    """A figure, once "Round 6", "R2" and "Team 2" (names, not figures) are set aside."""
    return bool(re.search(r"\d", _NAMES.sub("", s)))


def without_steps(text: str) -> str:
    """The text less its sentences about what Claude is doing. A sentence with a figure or a
    quotation always stays."""
    paragraphs: list[str] = []
    for paragraph in re.split(r"\n{2,}", text.strip()):
        lines: list[str] = []
        for line in paragraph.split("\n"):
            kept = []
            for sentence in SENTENCE_BREAK.split(line):
                plain = sentence.replace("’", "'").replace("‘", "'")
                if (_quotes_figure(sentence) or re.search(r'["“”]', sentence)
                        or not (_STARTS.search(plain) or _REPORTS.search(plain))):
                    kept.append(sentence)
            if kept:
                lines.append(" ".join(kept))
        if lines:
            paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs).strip()


PREAMBLE, FULL_ANSWER = 160, 200


def compose(rounds: list[str]) -> str:
    """Every turn's text as one answer. When the last turn is a full answer and the earlier ones
    only open it, the last stands alone. Never empty."""
    raw = "\n\n".join(p for p in rounds if p.strip())
    parts = [p for p in (without_steps(r) for r in rounds) if p]
    last = parts[-1] if parts else ""
    earlier = parts[:-1]
    preamble = bool(earlier) and all(len(p) <= PREAMBLE and not _quotes_figure(p) for p in earlier)
    answer = last if preamble and len(last) >= FULL_ANSWER else "\n\n".join(parts)
    return answer if answer else raw


# ---------------------------------------------------------------------------- loop helpers
def announces_lookups(text: str) -> bool:
    """A reply that only says what Claude is about to look up ("I'll look at …"), not an answer."""
    return text == "" or (len(text) < 400 and bool(re.match(
        r"^(I'll|I will|I'm going to|Let me|First,? I|To answer (this|that),? I)\b", text, re.IGNORECASE)))
