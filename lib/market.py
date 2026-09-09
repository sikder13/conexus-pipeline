"""Market context, per industry family rather than per company.

WHY PER FAMILY

The demand direction for metal fabrication is the same sentence for every metal
fabricator in the dataset. Fetching it once per company would make sixteen
requests to somebody's server for one answer, and — worse — would let sixteen
companies end up with sixteen slightly different versions of a fact that has
only one version. So it is fetched once, stored against the family, and reused.

WHAT MAY BE SAID, AND HOW IT IS KEPT HONEST

Three questions only: where demand is going, what is happening to labour, and
what the segment is adopting. Each answer must be pinned to a VERBATIM QUOTE
from a page we actually fetched, and the quote is checked against that page's
text before the answer is stored. A statement whose quote is not in the source
is discarded, which makes fabrication a mechanical impossibility rather than a
thing we ask a model not to do.

Tiers follow CLAUDE.md rule 6 and not convenience. A government statistical
page is a government record and is T1. Trade press and association publications
are T2 and may be stated only with the publication named. Nothing here is ever
T3: an aggregator's market-size estimate is exactly the kind of number this
pipeline refuses to repeat, however confidently it is printed.

WHEN THERE IS NOTHING

`NO_CONTEXT` is stored and rendered. A family with no usable sources gets a
recorded absence, never a paragraph of plausible-sounding trend language. The
whole point of the quote check is that padding cannot survive it, and the
correct output of a check that nothing passed is nothing.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from lib.claims import Tier, make_claim

NO_CONTEXT = "no reliable market context found"
"""Stored for a family whose sources yielded nothing that survived the check."""

DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("demand", "where demand in this segment is going"),
    ("labour", "what is happening to the workforce in this segment"),
    ("technology", "what this segment is adopting, and how quickly"),
)
"""The only three questions this module answers.

Deliberately few. A market section that tries to say everything says nothing an
operator can use on a call, and every extra question is another chance to
publish something we cannot source."""

MAX_SOURCE_CHARS = 18_000
"""How much of a fetched page is handed on for reading. Enough for the body of a
statistical page or an article; short enough that a whole batch stays cheap."""


class Source(NamedTuple):
    """One page we are prepared to read for market context, and what it is."""

    url: str
    tier: int
    what: str
    """What this source IS, in the words the analysis will use to attribute it."""


BLS = "https://www.bls.gov/iag/tgs/iag{}.htm"


def _gov(naics: str, words: str) -> Source:
    """A Bureau of Labor Statistics industry page — a government record, so T1."""
    return Source(BLS.format(naics), int(Tier.T1),
                  f"the Bureau of Labor Statistics industry page for {words}")


DEMAND = Source(
    "https://www.federalreserve.gov/releases/g17/current/default.htm",
    int(Tier.T1),
    "the Federal Reserve's monthly industrial production release",
)
"""Where output is going, from a government record rather than a forecast.

The first version of this list used a trade association's survey landing page,
and every family came back with nothing from it — correctly, because the page
describes the survey rather than reporting it. That is the quote check earning
its place on the first run: a weak source produced a recorded absence instead of
a paragraph of confident trend language.

Cross-industry, so it is the weakest source here and the analysis has to
attribute it as what it is. A statement true of all manufacturing is rarely the
one that makes a particular shop lean forward."""

FAMILY_SOURCES: dict[str, tuple[Source, ...]] = {
    "metal_fabrication": (
        _gov("332", "fabricated metal product manufacturing"),
        Source("https://www.amtonline.org/resources/industry-news", int(Tier.T2),
               "the Association For Manufacturing Technology's industry news"),
        DEMAND,
    ),
    "machinery_equipment": (
        _gov("333", "machinery manufacturing"),
        Source("https://www.amtonline.org/resources/industry-news", int(Tier.T2),
               "the Association For Manufacturing Technology's industry news"),
        DEMAND,
    ),
    "automotive": (
        _gov("336", "transportation equipment manufacturing"),
        DEMAND,
    ),
    "aerospace_defense": (
        _gov("336", "transportation equipment manufacturing"),
        DEMAND,
    ),
    "medical_device": (
        _gov("339", "miscellaneous manufacturing, which is where medical "
                    "equipment is counted"),
        DEMAND,
    ),
    "food_beverage": (
        _gov("311", "food manufacturing"),
        DEMAND,
    ),
    "plastics_rubber": (
        _gov("326", "plastics and rubber products manufacturing"),
        DEMAND,
    ),
    "wood_furniture": (
        _gov("321", "wood product manufacturing"),
        _gov("337", "furniture and related product manufacturing"),
        DEMAND,
    ),
    "printing_packaging": (
        _gov("323", "printing and related support activities"),
        DEMAND,
    ),
    "chemicals_coatings": (
        _gov("325", "chemical manufacturing"),
        DEMAND,
    ),
    "electronics": (
        _gov("334", "computer and electronic product manufacturing"),
        DEMAND,
    ),
    "building_products": (
        _gov("327", "nonmetallic mineral product manufacturing"),
        DEMAND,
    ),
    "agriculture": (
        _gov("333", "machinery manufacturing, which is where agricultural "
                    "equipment is counted"),
        DEMAND,
    ),
    "textiles_apparel": (
        _gov("313", "textile mills"),
        DEMAND,
    ),
    "lab_services": (
        _gov("334", "computer and electronic product manufacturing, which is "
                    "where analytical instruments are counted"),
        DEMAND,
    ),
}
"""Where each family's context is read from.

Curated rather than searched at run time, on purpose: a search result is a
different page every month and a market section that silently changes its
sources is a market section nobody can check. Adding a family here is a visible
edit with a commit message attached.

`unclassified` is absent. A company we could not place has no segment, and
inventing one for it would be the market equivalent of the peer group that
compares a company against the other companies we could not place."""


# ------------------------------------------------------- the Canadian market

CIS = "https://ised-isde.canada.ca/app/ixb/cis/summary-sommaire/{}"
"""Canadian Industry Statistics, published per NAICS by Innovation, Science and
Economic Development Canada. A government record, so T1."""


def _cis(naics: str, words: str) -> Source:
    return Source(CIS.format(naics), int(Tier.T1),
                  f"Innovation, Science and Economic Development Canada's "
                  f"Canadian Industry Statistics page for {words}")


STATCAN = Source(
    "https://www.statcan.gc.ca/en/subjects-start/manufacturing",
    int(Tier.T1),
    "Statistics Canada's manufacturing subject page",
)
"""The Canadian counterpart of the Federal Reserve source above, and the same
caveat applies: it is cross-industry, so it is the weakest source here and a
statement true of all Canadian manufacturing rarely makes one shop lean
forward."""

CANADA_FAMILY_SOURCES: dict[str, tuple[Source, ...]] = {
    family: (_cis(naics, words), STATCAN)
    for family, naics, words in (
        ("metal_fabrication", "332", "fabricated metal product manufacturing"),
        ("machinery_equipment", "333", "machinery manufacturing"),
        ("automotive", "336", "transportation equipment manufacturing"),
        ("aerospace_defense", "336", "transportation equipment manufacturing"),
        ("medical_device", "339", "miscellaneous manufacturing, which is where "
                                  "medical equipment is counted"),
        ("food_beverage", "311", "food manufacturing"),
        ("plastics_rubber", "326", "plastics and rubber products manufacturing"),
        ("wood_furniture", "321", "wood product manufacturing"),
        ("printing_packaging", "323", "printing and related support activities"),
        ("chemicals_coatings", "325", "chemical manufacturing"),
        ("electronics", "334", "computer and electronic product manufacturing"),
        ("building_products", "327", "non-metallic mineral product manufacturing"),
        ("agriculture", "333", "machinery manufacturing, which is where "
                               "agricultural equipment is counted"),
        ("textiles_apparel", "313", "textile mills"),
        ("lab_services", "334", "computer and electronic product manufacturing, "
                                "which is where analytical instruments are counted"),
    )
}
"""Where a Canadian company's context is read from.

Canadian sources rather than American ones, because the question the analysis
asks is where demand in THIS company's segment is going, and a Bureau of Labor
Statistics page answers it about a different country. The NAICS codes are the
same in both — it is a shared North American classification — which is why the
mapping mirrors FAMILY_SOURCES rather than inventing a second taxonomy.

Kept to government records. The trade-association pages that would be the
natural T2 additions — Canadian Manufacturers & Exporters among them — refuse an
identified crawler with a 403, and a source we cannot fetch is a source that can
only produce a recorded absence. When one becomes readable it is a visible edit
here with a commit message attached.

Provincial ministries are absent for the same reason plus one more: their
industry pages are promotional rather than statistical, and a page written to
attract investment is not a page to quote a trend from."""

SOURCES_BY_ADAPTER: dict[str, dict[str, tuple[Source, ...]]] = {
    "conexus_iedc": FAMILY_SOURCES,
    "canada_gc": CANADA_FAMILY_SOURCES,
}
"""Which country's record answers the question, keyed the way everything else is."""


def context_key(family: str, adapter: str | None = None) -> str:
    """The cache key one family's context is stored under.

    Market context is a property of a segment IN A MARKET. "Metal fabrication"
    is not one fact: the Canadian answer and the Indiana answer are different
    sentences read off different governments' pages, and storing them under one
    key would give an Ontario machine shop a paragraph about Indiana.

    Indiana keeps the bare family name so the rows already gathered under it
    stay addressable; every other source is prefixed. That asymmetry is a
    migration artefact rather than a principle, and it is cheaper than rewriting
    eleven stored rows to make a key look tidy.
    """
    from lib.scoring import DEFAULT_ADAPTER

    resolved = adapter or DEFAULT_ADAPTER
    return family if resolved == DEFAULT_ADAPTER else f"{resolved}:{family}"


def sources_for(family: str, adapter: str | None = None) -> tuple[Source, ...]:
    """The pages to read for one family, or nothing when we do not cover it."""
    from lib.scoring import DEFAULT_ADAPTER

    table = SOURCES_BY_ADAPTER.get(adapter or DEFAULT_ADAPTER, {})
    return table.get(family, ())


# ------------------------------------------------------------------ the check

def normalise(text: str) -> str:
    """Squash a page or a quote to the form both are compared in."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def quote_is_in(quote: str, page_text: str) -> bool:
    """Whether a quote really appears in the page it claims to come from.

    The whole honesty guarantee of this module. A statement is kept only when
    its quote survives this, so a paragraph of plausible trend language with no
    source behind it cannot be stored however fluently it was written.

    Short quotes are refused outright: three words will match almost any page
    and would turn the check into a formality.
    """
    cleaned = normalise(quote)
    if len(cleaned.split()) < 6:
        return False
    return cleaned in normalise(page_text)


class Statement(NamedTuple):
    """One thing we are prepared to say about a segment, and its receipt."""

    dimension: str
    statement: str
    quote: str
    source_url: str
    tier: int
    what: str

    def as_claim(self) -> dict[str, Any]:
        """The statement in the shape every other fact in this pipeline takes."""
        claim = make_claim(self.statement, Tier(self.tier), self.source_url)
        claim["market_dimension"] = self.dimension
        claim["quote"] = self.quote
        claim["source_kind"] = self.what
        return claim


def verify(
    candidates: list[dict[str, Any]], fetched: dict[str, str],
    sources: tuple[Source, ...],
) -> tuple[list[Statement], list[str]]:
    """Keep the statements whose quotes are really in the page they name.

    Returns the survivors and, separately, why each casualty was dropped —
    because a market section that quietly shrinks teaches nobody anything, and
    the discards are the most interesting thing a first run produces.
    """
    by_url = {s.url: s for s in sources}
    kept: list[Statement] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for entry in candidates:
        dimension = str(entry.get("dimension") or "").strip().lower()
        statement = str(entry.get("statement") or "").strip()
        quote = str(entry.get("quote") or "").strip()
        url = str(entry.get("source_url") or "").strip()
        if dimension not in {name for name, _ in DIMENSIONS}:
            dropped.append(f"{dimension!r} is not one of the three questions")
            continue
        if dimension in seen:
            dropped.append(f"a second answer for {dimension}; one is enough")
            continue
        if url not in by_url:
            dropped.append(f"{dimension}: names {url or 'no source'}, which was not read")
            continue
        if not statement:
            dropped.append(f"{dimension}: no statement")
            continue
        if not quote_is_in(quote, fetched.get(url, "")):
            dropped.append(
                f"{dimension}: the quote is not in {url} — discarded rather than "
                f"stored on trust")
            continue
        source = by_url[url]
        kept.append(Statement(dimension, statement, quote, url, source.tier, source.what))
        seen.add(dimension)
    return kept, dropped


# ------------------------------------------------------------------- prompting

SYSTEM = (
    "You are reading source pages about one segment of American manufacturing "
    "and reporting only what those pages actually say.\n\n"
    "Answer at most three questions, one entry each:\n"
    "  demand — where demand in this segment is going\n"
    "  labour — what is happening to the workforce in this segment\n"
    "  technology — what this segment is adopting, and how quickly\n\n"
    "RULES, and the first one is the whole job:\n"
    "1. Every entry carries a VERBATIM QUOTE of at least six words, copied "
    "character for character from the source text you were given. The quote is "
    "checked against the page. An entry whose quote is not found is thrown "
    "away, so inventing one wastes the entry.\n"
    "2. Answer only the questions the sources support. Two well-sourced answers "
    "beat three where one was stretched. Returning one entry, or none, is a "
    "correct answer when that is what the pages say.\n"
    "3. The statement is your one-sentence reading of the quote, in plain words "
    "an operations person would use. It must not go beyond what the quote "
    "supports.\n"
    "4. Say nothing about any individual company.\n"
    "5. Never write a market size, a growth percentage, or a forecast that is "
    "not in a quote you are also supplying.\n"
)

FORMAT = (
    "FORMAT. Reply with one delimited block and nothing else:\n\n"
    "  <<<MAP market>>>\n"
    '  {"entries": [\n'
    '     {"dimension": "demand", "statement": "...", "quote": "...", '
    '"source_url": "..."}\n'
    "  ]}\n"
    "  <<<END>>>\n\n"
    "source_url must be copied exactly from a SOURCE header below.\n"
)


def build_prompt(family: str, words: str, fetched: list[tuple[Source, str]]) -> str:
    """Everything the reader is allowed to see for one family."""
    parts = [
        f"SEGMENT: {words} ({family}).",
        "You may use only the sources below. Nothing else you know about this "
        "segment may appear in an entry.",
    ]
    for source, text in fetched:
        parts.append(
            f"--- SOURCE {source.url}\n"
            f"WHAT IT IS: {source.what}\n"
            f"TEXT:\n{text[:MAX_SOURCE_CHARS]}"
        )
    parts.append(FORMAT)
    return "\n\n".join(parts)


# ------------------------------------------------------------------ rendering

def as_prompt_block(context: dict[str, Any] | None) -> str:
    """One family's market context as prompt text for the analysis.

    Handed over as conclusions with their attributions attached, the same way
    the peer table is: the analysis may reason about what a trend MEANS for this
    company and may not add a trend of its own.
    """
    if not context or not context.get("statements"):
        return (
            "MARKET CONTEXT: none. We found no source we were willing to stand "
            "behind for this segment, so the analysis must say that plainly and "
            "must NOT substitute general knowledge about the industry."
        )
    lines = [
        "MARKET CONTEXT for this segment. These are the ONLY market statements "
        "you may make. Each carries the source it came from; attribute anything "
        "you use, in words, in the sentence that uses it. Do not add a trend, a "
        "market size, or a forecast that is not below.",
    ]
    for entry in context["statements"]:
        lines.append(
            f"- {entry['dimension']}: {entry['statement']} "
            f"[source: {entry['what']}, {entry['source_url']}]"
        )
    return "\n".join(lines)


def summarise(family: str, statements: list[Statement], dropped: list[str],
              sources: tuple[Source, ...]) -> dict[str, Any]:
    """One family's context as plain data, for storage and for the console."""
    return {
        "family": family,
        "statements": [
            {"dimension": s.dimension, "statement": s.statement, "quote": s.quote,
             "source_url": s.source_url, "tier": s.tier, "what": s.what}
            for s in statements
        ],
        "sources_read": [s.url for s in sources],
        "discarded": dropped,
        "note": NO_CONTEXT if not statements else "",
    }
