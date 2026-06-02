"""Extract buyback announcements from filing text.

Pure functions here (no network) so they are easy to unit test:

- ``html_to_text`` flattens an HTML/SGML filing into plain text.
- ``find_buyback_matches`` scans text for the configured buyback phrases and,
  for each match, captures a context snippet, a best-effort dollar amount, and
  classifies the match as a *new authorization* versus a *reference* to an
  existing program (e.g. quarterly execution disclosure).
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning

from app.config import settings

# Some EDGAR primary documents are served as XML. Parsing them with the HTML
# parser still yields usable text, so silence the (benign) advisory warning.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Event classifications for a buyback phrase occurrence.
EVENT_NEW_AUTHORIZATION = "new_authorization"
EVENT_REFERENCE = "reference"

# Maps amount-scale words to multipliers. Only reasonably unambiguous forms
# are accepted so we don't mis-parse stray letters.
_SCALE_MULTIPLIERS: dict[str, float] = {
    "billion": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "million": 1_000_000.0,
    "mn": 1_000_000.0,
    "thousand": 1_000.0,
}

_SCALE_ALT = "billion|million|thousand|bn|mn"

# Matches dollar figures such as "$5.0 billion", "$500 million",
# "$1,000,000,000", "$25 billion".
_AMOUNT_RE = re.compile(
    r"\$\s?(?P<number>[0-9][0-9,]*(?:\.[0-9]+)?)\s*"
    r"(?P<scale>" + _SCALE_ALT + r")?",
    re.IGNORECASE,
)

# A dollar amount that immediately follows an authorization verb / phrase, e.g.
# "authorized the repurchase of up to $5 billion" or "approved an additional
# $10 billion". This is a much stronger signal of the *authorization* size than
# the nearest dollar figure (which can be an unrelated number).
_AUTH_AMOUNT_RE = re.compile(
    r"\b(?:authoriz\w+|approv\w+|increas\w+|additional|up to|aggregate of|"
    r"program(?:\s+\w+){0,3}?\s+(?:of|totaling))\b"
    r"[^.$]{0,140}?(\$\s?[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:" + _SCALE_ALT + r")?)",
    re.IGNORECASE,
)

# An authorizing action verb. The presence of one of these *near* the matched
# buyback phrase is what separates an actual authorization from a passing
# mention of "our share repurchase program".
_AUTH_VERB_RE = re.compile(
    r"\b(?:authoriz\w+|re-?authoriz\w+|approv\w+|adopt\w+|"
    r"establish\w+|announc\w+)\b",
    re.IGNORECASE,
)

# Language that signals the authorization is *new or expanded* (as opposed to a
# restatement of an existing program). Used to keep genuine 10-K/10-Q
# announcements that lack an explicit board-action date.
_EXPANSION_RE = re.compile(
    r"\b(?:additional|increas\w+|newly|expand\w+|re-?authoriz\w+|"
    r"new\s+(?:\$|share|stock|buyback|repurchase))\b",
    re.IGNORECASE,
)

# How close (in characters) an authorizing verb must be to the matched phrase
# to count as authorizing *that* program.
_AUTH_PROXIMITY = 90

_MONTHS = (
    "January|February|March|April|May|June|July|"
    "August|September|October|November|December"
)
_MONTH_INDEX = {
    name.lower(): i
    for i, name in enumerate(_MONTHS.split("|"), start=1)
}
_DATE_RE = re.compile(
    r"\b(" + _MONTHS + r")\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)

# Phrases that indicate the filing is *executing under* or *referring to* an
# already-existing program rather than announcing a new one. Weighted; a total
# at or above the threshold marks the occurrence as a reference.
_REFERENCE_SIGNALS: dict[str, int] = {
    "repurchased": 3,
    "previously announced": 3,
    "previously authorized": 3,
    "does not obligate": 2,
    "during the": 2,
    "under the program": 2,
    "under this program": 2,
    "under the repurchase": 2,
    "available for repurchase": 2,
    "previously": 2,
    "remaining": 1,
    "remained available": 1,
    "completed": 1,
    "to date": 1,
    "ongoing": 1,
    "existing": 1,
    "current": 1,
    "maintains": 1,
    "as of": 1,
}
_REFERENCE_THRESHOLD = 2

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class Match:
    """A single buyback phrase occurrence within a document."""

    matched_token: str
    amount_context: str
    authorization_amount: float | None
    authorization_amount_text: str | None
    event_type: str
    authorization_date: date | None = None
    # True when the surrounding text uses new/expanded-authorization language
    # (e.g. "additional", "increased", "new $X program").
    has_expansion_language: bool = False


def html_to_text(content: str) -> str:
    """Flatten HTML/SGML filing content into normalized plain text."""

    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _token_pattern(token: str) -> re.Pattern[str]:
    """Build a whitespace-tolerant, case-insensitive pattern for a phrase."""

    parts = [re.escape(word) for word in token.split()]
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.IGNORECASE)


def _parse_amount(raw_number: str, scale: str | None) -> float | None:
    try:
        value = float(raw_number.replace(",", ""))
    except ValueError:
        return None
    if scale:
        value *= _SCALE_MULTIPLIERS.get(scale.lower(), 1.0)
    return value


def _amount_from_text(fragment: str) -> tuple[float | None, str | None]:
    """Parse the first dollar figure found in ``fragment``."""

    m = _AMOUNT_RE.search(fragment)
    if not m:
        return None, None
    value = _parse_amount(m.group("number"), m.group("scale"))
    if value is None:
        return None, None
    return value, _WHITESPACE_RE.sub(" ", m.group(0)).strip()


def _distance_to(anchor: int, start: int, end: int) -> int:
    """Distance from position ``anchor`` to the span ``[start, end)``."""

    if end <= anchor:
        return anchor - end
    if start >= anchor:
        return start - anchor
    return 0


def _authorization_amount(
    context: str, anchor: int
) -> tuple[float | None, str | None]:
    """Authorization-verb dollar amount closest to ``anchor``, if any.

    Anchoring to the token position keeps the right amount when a filing
    describes more than one authorization.
    """

    best: tuple[int, float, str] | None = None
    for m in _AUTH_AMOUNT_RE.finditer(context):
        value, raw_text = _amount_from_text(m.group(1))
        if value is None:
            continue
        gstart, gend = m.span(1)
        distance = _distance_to(anchor, gstart, gend)
        if best is None or distance < best[0]:
            best = (distance, value, raw_text)
    if best is None:
        return None, None
    return best[1], best[2]


def _nearest_amount(
    text: str, match_start: int, match_end: int
) -> tuple[float | None, str | None]:
    """Find the dollar amount closest to a token match within ``text``.

    Distance is measured from the nearest edge of the token match to the
    amount. Returns ``(value, raw_text)`` or ``(None, None)``.
    """

    best: tuple[int, float, str] | None = None  # (distance, value, raw_text)
    for m in _AMOUNT_RE.finditer(text):
        value = _parse_amount(m.group("number"), m.group("scale"))
        if value is None:
            continue
        if m.end() <= match_start:
            distance = match_start - m.end()
        elif m.start() >= match_end:
            distance = m.start() - match_end
        else:
            distance = 0
        raw_text = _WHITESPACE_RE.sub(" ", m.group(0)).strip()
        if best is None or distance < best[0]:
            best = (distance, value, raw_text)
    if best is None:
        return None, None
    return best[1], best[2]


def _parse_text_date(month: str, day: str, year: str) -> date | None:
    try:
        return date(int(year), _MONTH_INDEX[month.lower()], int(day))
    except (ValueError, KeyError):
        return None


def _nearest_date(context: str, anchor: int) -> date | None:
    """Return the explicit calendar date closest to ``anchor`` in ``context``."""

    best: tuple[int, date] | None = None
    for m in _DATE_RE.finditer(context):
        parsed = _parse_text_date(m.group(1), m.group(2), m.group(3))
        if parsed is None:
            continue
        midpoint = (m.start() + m.end()) // 2
        distance = abs(midpoint - anchor)
        if best is None or distance < best[0]:
            best = (distance, parsed)
    return best[1] if best else None


def _reference_score(context_lower: str) -> int:
    return sum(
        weight
        for phrase, weight in _REFERENCE_SIGNALS.items()
        if phrase in context_lower
    )


def _closest_auth_verb(context: str, anchor: int) -> re.Match[str] | None:
    """Authorizing verb closest to ``anchor`` (the matched phrase), if any."""

    best: tuple[int, re.Match[str]] | None = None
    for m in _AUTH_VERB_RE.finditer(context):
        distance = _distance_to(anchor, m.start(), m.end())
        if best is None or distance < best[0]:
            best = (distance, m)
    return best[1] if best else None


def _classify(context: str, anchor: int) -> tuple[str, re.Match[str] | None]:
    """Classify an occurrence as a new authorization or a reference.

    An occurrence is a *new authorization* only when an authorizing verb
    (authorized / approved / adopted / announced ...) sits close to the matched
    phrase AND execution/reference language ("repurchased", "during the",
    "previously", ...) does not dominate the surrounding text. Everything else
    -- including bare mentions of an existing program and quarterly execution
    disclosures -- is treated as a reference.
    """

    auth_verb = _closest_auth_verb(context, anchor)
    near_auth = (
        auth_verb is not None
        and _distance_to(anchor, auth_verb.start(), auth_verb.end())
        <= _AUTH_PROXIMITY
    )
    reference_dominates = _reference_score(context.lower()) >= _REFERENCE_THRESHOLD
    if near_auth and not reference_dominates:
        return EVENT_NEW_AUTHORIZATION, auth_verb
    return EVENT_REFERENCE, None


def _build_match(
    text: str,
    token: str,
    found: re.Match[str],
    window: int,
) -> Match:
    ctx_start = max(0, found.start() - window)
    ctx_end = min(len(text), found.end() + window)
    # Extraction runs on the raw slice so token offsets stay valid (the regexes
    # are whitespace-tolerant); only the displayed snippet is normalized.
    raw_context = text[ctx_start:ctx_end]
    local_start = found.start() - ctx_start
    local_end = found.end() - ctx_start

    event_type, auth_verb = _classify(raw_context, local_start)

    # Only attribute an authorization amount/date to genuine authorizations.
    # For references the nearby figure is usually an execution amount, not the
    # authorization size, so we leave it unset.
    amount: float | None = None
    amount_text: str | None = None
    authorization_date: date | None = None
    has_expansion = False
    if event_type == EVENT_NEW_AUTHORIZATION:
        amount, amount_text = _authorization_amount(raw_context, local_start)
        if amount is None:
            amount, amount_text = _nearest_amount(
                raw_context, local_start, local_end
            )
        anchor = auth_verb.start() if auth_verb else local_start
        authorization_date = _nearest_date(raw_context, anchor)
        has_expansion = _EXPANSION_RE.search(raw_context) is not None

    return Match(
        matched_token=token,
        amount_context=_WHITESPACE_RE.sub(" ", raw_context).strip(),
        authorization_amount=amount,
        authorization_amount_text=amount_text,
        event_type=event_type,
        authorization_date=authorization_date,
        has_expansion_language=has_expansion,
    )


def find_buyback_matches(
    text: str,
    *,
    tokens: tuple[str, ...] | None = None,
    context_window: int | None = None,
) -> list[Match]:
    """Scan ``text`` for buyback phrases and classify each occurrence.

    For every token we scan all occurrences. If any are classified as a new
    authorization we return those (deduplicated by authorization date + amount),
    so a genuine new authorization is not masked by an earlier execution
    reference in the same document. Otherwise we return a single reference
    occurrence for the token.
    """

    tokens = tokens or settings.buyback_tokens
    window = context_window or settings.context_window

    results: list[Match] = []
    for token in tokens:
        pattern = _token_pattern(token)
        new_matches: list[Match] = []
        first_reference: Match | None = None
        seen_new: set[tuple[date | None, float | None]] = set()

        for found in pattern.finditer(text):
            match = _build_match(text, token, found, window)
            if match.event_type == EVENT_NEW_AUTHORIZATION:
                amount_key = (
                    round(match.authorization_amount, 2)
                    if match.authorization_amount is not None
                    else None
                )
                key = (match.authorization_date, amount_key)
                if key in seen_new:
                    continue
                seen_new.add(key)
                new_matches.append(match)
            elif first_reference is None:
                first_reference = match

        if new_matches:
            results.extend(new_matches)
        elif first_reference is not None:
            results.append(first_reference)

    return results
