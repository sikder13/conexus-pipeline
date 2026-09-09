"""Which recipients are businesses — the filter, and why each exclusion fires.

WHAT THIS DECIDES

The dataset funds everybody: cities, universities, hospitals, charities, band
councils, and about a hundred thousand private individuals. We sell to owner-run
manufacturers and distributors, so everything else has to go — and it has to go
with a reason attached, because a filter that reports only a number is a filter
nobody can check.

TWO SIGNALS, IN A DELIBERATE ORDER

The dataset publishes a `recipient_type` code, and where it is populated it is
the government's own classification of the recipient. That is a stronger signal
than anything we could infer from a name, so it decides most rows.

It is not sufficient on its own for two reasons. It is blank on 72,456 of the
445,216 Ontario and Alberta agreements, and where it is blank there is nothing
to read but the name. And it is occasionally wrong: institutions do appear coded
as for-profit. So the name test runs first, over every row, as a backstop
against a miscoded institution — and the code decides the rest.

WHERE WE REFUSE TO GUESS

An unclassified recipient is kept only when its name affirmatively carries a
corporate form: Inc, Ltd, Limitée, Corp, ULC. "Greg Stewart" and "K. Fred &
Sharon Judd" are excluded not because we decided they are individuals but
because nothing in the record says they are a company. That direction of doubt
is the correct one here: including a private individual in a prospect list is a
CASL problem and a dignity problem, and excluding a real company costs us one
lead that the top-30 program report will show the operator anyway.

Indigenous recipients (code A) are held to the same name test rather than
excluded outright. The code covers band councils and Indigenous-owned
corporations alike, and an incorporated business is a business.
"""

from __future__ import annotations

import re
from typing import NamedTuple

BUSINESS_CODE = "F"
"""`recipient_type` for a for-profit organization, per the published dictionary."""

CODE_EXCLUSIONS: dict[str, tuple[str, str]] = {
    "G": ("government", "recipient type G — Government"),
    "N": ("nonprofit", "recipient type N — Not-for-profit organizations and charities"),
    "S": ("academia", "recipient type S — Academia"),
    "I": ("international", "recipient type I — International (non-government)"),
    "P": ("individual", "recipient type P — Individual or sole proprietorships"),
}
"""Codes that settle the question on their own, and the words the dictionary uses.

Absent from this table on purpose: A (Indigenous recipients) and O (Other), both
of which mix incorporated businesses with bodies that are not, and the blank
code. Those fall through to the name test."""

NAME_TESTED_CODES: frozenset[str] = frozenset({"A", "O", ""})
"""Codes that carry no verdict, so the recipient's name has to answer."""

INSTITUTION_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "municipality",
        r"\b(?:(?:city|town|township|village|municipality|municipalit[ée]|"
        r"county|counties|district|hamlet|borough|parish) of\b|"
        r"corporation of the\b|(?:regional |rural )?municipalit(?:y|[ée])\b|"
        r"ville de\b|cit[ée] de\b|canton de\b)",
        "a municipality",
    ),
    (
        "academia",
        r"\b(?:university|universit[ée]|college|coll[èe]ge|polytechnic|c[ée]gep|"
        r"school board|school district|conseil scolaire|academy of|"
        r"institute of technology)\b",
        "a university, college or school authority",
    ),
    (
        "hospital",
        r"\b(?:(?<!animal )(?<!veterinary )(?:hospital|h[ôo]pital)|"
        r"health (?:centre|center|authority|network|sciences|region|unit)|"
        r"regional health|centre de sant[ée]|"
        r"long[- ]term care (?:home|centre))\b",
        "a hospital or health authority",
    ),
    (
        "nonprofit",
        r"\b(?:association|soci[ée]t[ée]|society|foundation|fondation|charity|"
        r"charitable|club|federation|f[ée]d[ée]ration|chamber of commerce|"
        r"united way|not[- ]for[- ]profit|non[- ]profit|nonprofit|"
        r"volunteer|church|parish|synagogue|mosque|diocese|ministries|"
        r"food bank|community living|ymca|ywca|legion)\b",
        "a not-for-profit, charity or association",
    ),
    (
        "government",
        r"\b(?:government of|gouvernement du|his majesty|her majesty|the crown|"
        r"ministry of|minist[èe]re|department of|agence|first nation|"
        r"first nations|m[ée]tis nation|inuit|band council|tribal council|"
        r"public library|police service|conservation authority)\b",
        "a government body",
    ),
)
"""Name patterns that place a recipient outside the ICP whatever its code says.

Blunt on purpose, and every match is counted and reported with the word that
caused it, so an over-matching pattern shows up in the run report rather than
silently shrinking the list. The alternative — trusting `recipient_type` alone —
is what lets a school board arrive coded as a for-profit organization."""

CORPORATE_FORM = re.compile(
    r"(?:^|[\s,.(])(?:"
    r"inc|inc\.|incorporated|incorpor[ée]e|"
    r"ltd|ltd\.|limited|limit[ée]e|lt[ée]e|ltee|"
    r"corp|corp\.|corporation|"
    r"ulc|llp|l\.l\.p|llc|l\.l\.c|lp|"
    r"co|co\.|company|compagnie|cie|"
    r"s\.e\.n\.c|senc|s\.a\.r\.f|gmbh|pty"
    r")(?:$|[\s,.)])",
    re.IGNORECASE,
)
"""A legal form in the recipient's name — the one thing that lets an unclassified
recipient through. Deliberately restricted to incorporation markers: "Farms",
"Enterprises" and "Group" read as businesses to a person and are also how a
great many charities and band-owned bodies name themselves."""

_COMPILED = tuple(
    (kind, re.compile(pattern, re.IGNORECASE), words)
    for kind, pattern, words in INSTITUTION_PATTERNS
)


class RecipientVerdict(NamedTuple):
    """Whether one recipient is a business, and the reason either way."""

    is_business: bool
    reason: str
    detail: str

    @property
    def excluded(self) -> bool:
        return not self.is_business


def institution_match(name: str) -> tuple[str, str, str] | None:
    """The first institution pattern this name matches: (kind, words, matched text)."""
    for kind, pattern, words in _COMPILED:
        found = pattern.search(name or "")
        if found:
            return kind, words, found.group(0).strip()
    return None


def has_corporate_form(name: str) -> bool:
    """True when the name carries a legal form of incorporation."""
    return bool(CORPORATE_FORM.search(name or ""))


def classify_recipient(
    legal_name: str, operating_name: str | None, type_code: str | None
) -> RecipientVerdict:
    """Decide whether this recipient is a business we may prospect.

    Both names are read, because a numbered legal name with a trading name is
    common and the institution words appear in whichever one a person would
    recognise. The code is read second, so a miscoded institution is caught.
    """
    names = " / ".join(part for part in (legal_name, operating_name) if part)
    code = (type_code or "").strip().upper()

    found = institution_match(names)
    if found:
        kind, words, matched = found
        return RecipientVerdict(
            False, kind, f"the name says {words} (matched “{matched}”)"
        )

    if code in CODE_EXCLUSIONS:
        reason, detail = CODE_EXCLUSIONS[code]
        return RecipientVerdict(False, reason, detail)

    if code == BUSINESS_CODE:
        return RecipientVerdict(True, "", "recipient type F — For-profit organizations")

    if code in NAME_TESTED_CODES:
        if has_corporate_form(names):
            published = f"recipient type {code}" if code else "no recipient type published"
            return RecipientVerdict(
                True, "", f"{published}; the name carries a legal form of incorporation"
            )
        return RecipientVerdict(
            False,
            "no_corporate_form",
            f"{'recipient type ' + code if code else 'no recipient type published'} "
            f"and no legal form of incorporation in the name — an individual or an "
            f"unincorporated body as far as the record says",
        )

    return RecipientVerdict(
        False,
        "unknown_type",
        f"recipient type {code!r} is not one the published dictionary defines",
    )


def company_key(name: str | None) -> str:
    """The key two records of the same company collapse on.

    Built on the extractor's own `normalize_name` so that a company matched here
    is a company the extractor will also recognise as already in the database,
    rather than inserting it a second time under a different spelling. The
    Canadian legal forms the Indiana normaliser never had to know about — Ltée,
    Limitée, ULC, Cie — are stripped first, so "Fabrication Nadeau Ltée" and
    "Fabrication Nadeau Limitée" are one company.
    """
    from lib.sources.conexus import normalize_name

    stripped = _CANADIAN_FORMS.sub(" ", name or "")
    return normalize_name(stripped)


_CANADIAN_FORMS = re.compile(
    r"\b(?:limit[ée]e|lt[ée]e|ltee|limited|ulc|s\.e\.n\.c|senc|compagnie|cie)\b\.?",
    re.IGNORECASE,
)
"""Legal forms `normalize_name` does not strip, because Indiana has none of them."""
