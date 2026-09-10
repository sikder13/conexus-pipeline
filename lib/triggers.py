"""What happened at this company recently, and how recently it happened.

WHY ORDER BY THIS RATHER THAN BY SCORE

The signal score answers "is this company worth contacting". It does not answer
"is this week the week", and those are different questions with different
answers. A company that posted a clerical role eleven days ago and a company
that posted the same role two years ago score identically, and only one of them
has somebody inside it right now who has already decided the work is too much.

So the desk orders by trigger freshness and breaks ties on the score. The score
still decides who is in the queue; the trigger decides who is at the top of it
today, and it re-orders itself as the week passes without anybody touching it.

WHAT COUNTS AS A TRIGGER, AND WHAT DOES NOT

Three things, all of them dated by the source rather than by us:

* a job posting, dated on their own careers page;
* press or a programme announcement, dated by its publisher;
* an award, dated by the government record.

`date_checked` is deliberately NOT a trigger date. It records when we looked,
not when anything happened, and ordering by it would put the companies we
happened to crawl most recently at the top of the queue — which is a fact about
our crawler wearing the clothes of a fact about them. A claim with no source
date has no trigger, and a company with no trigger sorts last with its reason
printed rather than being given a date it does not have.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, NamedTuple

from lib.evidence import (
    BLOCK2_GRANT_FUNDED,
    BLOCK3_HIRING_SIGNALS,
    BLOCK7_PEOPLE,
    BLOCK8_FINANCIAL_SCALE,
    read_block,
)
from lib.integrity import is_usable

POSTING = "job posting"
NEWS = "press"
AWARD = "award"
NONE = "none"

NO_TRIGGER = "no dated event on file"

ISO_DATE = re.compile(r"\b(20\d\d)-(\d{2})-(\d{2})\b")
YEAR = re.compile(r"\b(20[0-2]\d)\b")


DAY = "day"
YEAR_ONLY = "year"


class Trigger(NamedTuple):
    """The most recent dated thing we know happened at one company."""

    kind: str
    when: date | None
    words: str
    """What the desk prints beside the row."""

    precision: str = DAY
    """How well the source dated it: to the day, or only to the year.

    Carried because the two are ordered the same way and said differently. A
    2026 award is sorted as recent, correctly, and printing "0 days ago" for it
    would tell an operator about to dial that something happened this morning."""

    @property
    def dated(self) -> bool:
        return self.when is not None

    def age_days(self, today: date | None = None) -> int | None:
        if self.when is None:
            return None
        return ((today or date.today()) - self.when).days

    def display(self, today: date | None = None) -> str:
        """The trigger as one short phrase, saying only what the source dated."""
        if not self.dated:
            return NO_TRIGGER
        now = today or date.today()
        if self.precision == YEAR_ONLY:
            if self.when.year == now.year:
                return f"{self.words} this year"
            return f"{self.words} in {self.when.year}"
        days = self.age_days(today)
        if days < 0:
            return f"{self.words} — dated ahead of today"
        if days < 45:
            return f"{self.words} — {days} days ago"
        if days < 400:
            return f"{self.words} — {days // 30} months ago"
        return f"{self.words} — {days // 365} years ago"


def _as_date(value: Any, today: date | None = None) -> tuple[date, str] | None:
    """A real calendar date out of whatever the claim carries, or None.

    A year-only record is dated to its year end rather than its start: an award
    recorded as "2025" happened at some point during 2025, and dating it to
    January would make it look staler than it is by up to a year.

    Except in the current year, where the year end has not happened. Capping at
    today is the closest true statement — the event is somewhere between January
    and now — and the alternative put every 2026 award three months into the
    future and printed "dated ahead of today" against the whole queue.
    """
    text = str(value or "")
    found = ISO_DATE.search(text)
    if found:
        try:
            return date(int(found.group(1)), int(found.group(2)),
                        int(found.group(3))), DAY
        except ValueError:
            return None
    year = YEAR.search(text)
    if year:
        now = today or date.today()
        return min(date(int(year.group(1)), 12, 31), now), YEAR_ONLY
    return None


def newest_posting(evidence: dict[str, Any] | None) -> tuple[date, str] | None:
    """The most recent posting date their own careers page showed.

    Undated postings are not counted here even though `job_postings` counts them
    toward the hiring flag. The two rules answer different questions: the flag
    asks whether they are hiring, and an undated posting is evidence that they
    are; this asks when, and an undated posting cannot say.
    """
    roles = read_block(evidence, BLOCK3_HIRING_SIGNALS).get("open_roles")
    best: tuple[date, str] | None = None
    for claim in roles if isinstance(roles, list) else []:
        if not isinstance(claim, dict) or not is_usable(claim):
            continue
        value = claim.get("value")
        posted = _as_date((value or {}).get("posted") if isinstance(value, dict) else None)
        if posted and (best is None or posted[0] > best[0]):
            best = posted
    return best


def newest_press(evidence: dict[str, Any] | None) -> tuple[date, str] | None:
    """The publication date of the most recent press or announcement claim."""
    best: tuple[date, str] | None = None
    for block in (BLOCK2_GRANT_FUNDED, BLOCK7_PEOPLE, BLOCK8_FINANCIAL_SCALE):
        body = read_block(evidence, block)
        for key, claim in body.items():
            if key in ("flags", "grant_year") or not isinstance(claim, dict):
                continue
            if not is_usable(claim) or claim.get("tier") != 2:
                continue
            when = _as_date(claim.get("value")) or _as_date(claim.get("published"))
            if when and (best is None or when[0] > best[0]):
                best = when
    return best


def award_date(prospect: dict[str, Any]) -> tuple[date, str] | None:
    """When their award was made, from the column both loaders write."""
    year = prospect.get("grant_year")
    if year:
        return _as_date(str(year))
    claim = read_block(prospect.get("evidence_file"), BLOCK2_GRANT_FUNDED).get("grant_year")
    return _as_date(claim.get("value")) if isinstance(claim, dict) else None


def trigger_for(prospect: dict[str, Any]) -> Trigger:
    """The freshest dated event at this company, and what to call it."""
    evidence = prospect.get("evidence_file") or {}
    found = [
        (POSTING, newest_posting(evidence), "posted a role"),
        (NEWS, newest_press(evidence), "named in the press"),
        (AWARD, award_date(prospect), "took the award"),
    ]
    dated = [Trigger(kind, when[0], words, when[1])
             for kind, when, words in found if when]
    if not dated:
        return Trigger(NONE, None, NO_TRIGGER)
    return max(dated, key=lambda c: c.when)


def sort_key(prospect: dict[str, Any], today: date | None = None) -> tuple:
    """Freshest trigger first, then the score. Undated companies sort last.

    Returned as a tuple rather than applied here so that a caller sorting rows
    that carry more than a prospect — an artifact and its company, say — can use
    the same ordering without this module knowing about artifacts.
    """
    trigger = trigger_for(prospect)
    days = trigger.age_days(today)
    return (
        0 if trigger.dated else 1,
        days if days is not None else 10**6,
        -(prospect.get("signal_score") or 0),
        str(prospect.get("company_name") or ""),
    )


def order(rows: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """Prospects in the order the outreach desk should work them."""
    return sorted(rows, key=lambda row: sort_key(row, today))
