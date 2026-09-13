"""Offer differentiation — shared patterns are allowed, shared sentences are not.

WHY THIS EXISTS

The operator measured the Indiana book and found ready companies leading with an
offer titled, word for word, "Quote Assembler" — three of five when they counted,
all five by the time this module measured it — and the approaches across both
countries collapsing into about six generic shapes. The
shapes are not the problem. Estimators really do assemble quotes by hand at a lot
of small manufacturers, and a costing template that fits that friction is
legitimately reused — `lib/roi_patterns.py` exists precisely so the arithmetic is
shared. What was wrong is that the PRESENTATION was shared too: the same title,
the same opening premise, and scope paragraphs that would paste into another
company's file without a word changing. A prospect who is sent that has been
sent a category, not an analysis of their business.

So this module draws the line where the operator drew it. It never asks for a
different pattern. It asks that each offer be bound to the company's own facts,
named with the company's own nouns, and opened on something observed about them.

THE FOUR RULES

1. **Binding.** An approach's scope cites at least two claims of the kinds that
   are genuinely theirs — see `SPECIFIC_KINDS`.
2. **Naming.** The title carries a noun from their own evidence: their equipment,
   product or process. Their company name does not count; stamping a name on an
   archetype is the cheapest possible fake of difference.
3. **Anchor.** The first sentence of the approach's scope cites one of those
   claims, so it opens on them rather than on the pattern's generic premise.
4. **Across the book.** No two companies share an offer title, and a scope
   paragraph that shares most of its wording with another company's is flagged.

WHAT IT REFUSES TO DO

Invent uniqueness. A company whose evidence holds fewer than two specific claims
is bound to all the claims it has and no more; a company whose evidence holds no
usable noun cannot be named by one, and the analyst falls back to the honest
shared pattern (see `fallback_title`) rather than coining a detail. The pattern id
is always kept, in the finmodel's `model_id`, so calibration can still count how
often each shape is sold.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any, NamedTuple

SPECIFIC_KINDS: tuple[tuple[str, str], ...] = (
    ("block2_grant_funded.what_the_grant_funded", "grant purchase"),
    ("block2_grant_funded.agreement_title", "grant purchase"),
    ("block2_grant_funded.agreement_description", "grant purchase"),
    ("block1_what_they_make.certifications", "certification"),
    ("block1_what_they_make.self_description", "product line"),
    ("block1_what_they_make.who_they_sell_to", "product line"),
    ("block1_what_they_make.products", "product line"),
    ("block3_hiring_signals.open_roles", "posting"),
    ("block6_tech_stack.", "stack"),
    ("block7_people.named_people", "person"),
    ("block7_people.leadership_quotes", "person"),
)
"""Claim paths that say something about THIS company, keyed to the rule's list.

Everything else a file holds is real but shared by construction. The program's
purpose text is identical for every recipient of the program. An award amount is
theirs, but it is a number, and the numbers already differ by construction. A
contact form, a working SSL certificate, a phone number on the page — every
company in the book has those, so a scope that leans on them has leaned on
nothing. Flags are excluded wherever they sit: a flag is our arithmetic over
evidence, not evidence."""

MIN_BINDING = 2
"""Company-specific claims an approach's scope must cite."""

NEAR_IDENTICAL = 0.80
"""Share of one scope's three-word runs found in another's before they are flagged."""

SHINGLE = 3
MIN_SHINGLES = 10
"""A paragraph shorter than this is too short to be called a copy of anything."""

_STOPWORD_TEXT = (
    "a an and are as at be by for from has have in into is it its of on or that "
    "the their them they this to was with we our your you"
)
STOPWORDS = frozenset(_STOPWORD_TEXT.split())

_GENERIC_TITLE_TEXT = """
    quote quotes quoting assembler assembly drafting draft drafter estimate estimates
    estimating estimator document documents documentation generator packet packets
    certificate certificates intake order orders ordering scheduling schedule
    scheduler diagnostic diagnostics record records report reports reporting
    utilisation utilization utilisation tracker tracking dashboard capture portal
    automation automated system systems tool tools pilot build audit review labour
    labor hours handoff handoffs reorder inventory maintenance weekly monthly daily
    workflow data entry digital single shared log logging sheet spreadsheet form
    forms request requests job jobs work shop floor plant facility company business
    manufacturing manufacturer production process processes operations customer
    customers product products service services team line lines front door website
    web site email inbox follow followup follow-up grant equipment machine machines
    qa quality inspection inspections compliance standard standards scrap waste
    throughput capacity cost costs time new first quick fast one
"""
GENERIC_TITLE_WORDS = frozenset(_GENERIC_TITLE_TEXT.split())
"""Words that name a pattern rather than a company.

A title built only from these could head any company's offer. The list is broad on
purpose: "Inspection certificate generator" names no company, whereas "ISO 9001
inspection packet builder" names one of theirs, because 9001 came from their own
certification claim."""

CITATION = re.compile(r"\[([a-z0-9_]+(?:\.[a-z0-9_]+(?:\[\d+\])?)+)\]")
"""The drafter's claim-reference pattern, restated so a shared library module does
not import a tool at load time.

Kept identical by a test rather than by hope: `tests/test_differentiation.py`
asserts the two patterns are equal, so they cannot drift the way the jargon
lists did."""

SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
LEADING_LABEL = re.compile(r"^\s*(scope|feasibility|the build)\s*:\s*", re.IGNORECASE)


class Evidence(NamedTuple):
    """What this company's file holds that is genuinely about this company."""

    paths: frozenset[str]
    words: frozenset[str]


class Offer(NamedTuple):
    """One approach, as stored in a company's live analysis."""

    company_id: str
    company: str
    country: str
    number: int
    name: str
    pitch: str
    prose: str
    pattern: str | None


# ------------------------------------------------------------------ evidence

def kind_of(path: str) -> str | None:
    """Which kind of company-specific claim a path is, or None."""
    base = re.sub(r"\[\d+\]", "", path or "")
    if ".flags." in base:
        return None
    for prefix, kind in SPECIFIC_KINDS:
        if base.startswith(prefix):
            return kind
    return None


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def specific_evidence(claims: list[tuple[str, dict[str, Any]]]) -> Evidence:
    """The company-specific claims in a file, and the words they are written in."""
    paths, words = set(), set()
    for path, claim in claims:
        if kind_of(path) is None:
            continue
        value = claim.get("value")
        if isinstance(value, bool) or value is None:
            continue
        paths.add(path)
        words.update(w for w in _words(str(value))
                     if (len(w) >= 3 or w.isdigit()) and w not in STOPWORDS)
    return Evidence(frozenset(paths), frozenset(words))


def specific_cited(text: str, evidence: Evidence) -> list[str]:
    """The distinct company-specific claims a passage cites, in order."""
    seen: list[str] = []
    for path in CITATION.findall(text or ""):
        if path in evidence.paths and path not in seen:
            seen.append(path)
    return seen


def sentences(text: str) -> list[str]:
    """A passage split into sentences, each keeping the citations that close it."""
    out: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text or ""):
        for piece in SENTENCE_END.split(paragraph.strip()):
            piece = piece.strip()
            if not piece:
                continue
            # A citation written after the full stop belongs to the sentence
            # before it, not to a sentence of its own.
            if out and piece.startswith("["):
                out[-1] = f"{out[-1]} {piece}"
            else:
                out.append(piece)
    return out


def facts_block(claims: list[tuple[str, dict[str, Any]]], limit: int = 24) -> str:
    """The company-specific evidence, listed for the generator to bind to.

    The full evidence is already in the prompt, but it lists every claim a file
    holds — the contact form and the program boilerplate beside the sleever
    lines — and asking for "two of the ones that are about them" across three
    approaches at once was a constraint the generator kept losing: it would bind
    one approach and let another slip, then trade them on the retry. A short
    list of only the eligible lines makes the choice a lookup instead of a
    judgement.
    """
    rows = []
    for path, claim in claims:
        kind = kind_of(path)
        value = claim.get("value")
        if kind is None or isinstance(value, bool) or value is None:
            continue
        text = " ".join(str(value).split())
        rows.append(f"  [{path}] ({kind}) {text[:160]}")
    if not rows:
        return ""
    return (
        "THEIR OWN FACTS. Only these lines count toward binding an approach to this "
        "company. For EACH approach, choose at least two of them that the approach "
        "genuinely touches, open the scope on one, and cite both by their ID. The "
        "same line may bind more than one approach.\n" + "\n".join(rows[:limit])
    )


def keep_notes(approaches: list[Any], evidence: Evidence) -> list[str]:
    """What an earlier attempt got right, so a retry does not undo it.

    Feedback that names only the failing approach invites the generator to
    rewrite all three and break one that was fine; with two attempts that is
    the whole budget. So the passing approaches are named, with their bindings,
    and the instruction is to leave them alone.
    """
    notes = []
    for approach in approaches:
        prose = getattr(approach, "prose", "")
        number = getattr(approach, "number", 0)
        if binding_failures(number, prose, evidence) or anchor_failures(number, prose, evidence):
            continue
        cited = specific_cited(prose, evidence)
        notes.append(
            f"approach {number} already binds to {', '.join(cited[:3])} and opens on "
            f"one of them — keep its scope and those citations exactly as they are, "
            f"and change only what the failures above name.")
    return notes


# --------------------------------------------------------------- rules 1 to 3

def binding_failures(number: int, prose: str, evidence: Evidence) -> list[str]:
    """Rule 1: the scope cites at least two claims that are about this company.

    A company whose file holds fewer than two such claims is held to all of them
    and no more. Asking for a second would be asking the generator to invent one.
    """
    need = min(MIN_BINDING, len(evidence.paths))
    cited = specific_cited(prose, evidence)
    if len(cited) >= need:
        return []
    loose = [s for s in sentences(prose) if not specific_cited(s, evidence)]
    shown = " | ".join(repr(LEADING_LABEL.sub("", s)[:110]) for s in loose[:3])
    return [
        f"approach {number}'s scope cites {len(cited)} claim(s) about this company "
        f"where {need} are required — their equipment or grant purchase, a "
        f"certification, a product line, a posting, their stack or a named "
        f"person. These sentences would paste into another company's file "
        f"unchanged: {shown}"
    ]


def anchor_failures(number: int, prose: str, evidence: Evidence) -> list[str]:
    """Rule 3: the scope opens on an observed fact about them, with its claim id."""
    if not evidence.paths:
        return []
    parts = sentences(prose)
    first = parts[0] if parts else ""
    if specific_cited(first, evidence):
        return []
    return [
        f"approach {number} opens on the pattern rather than on them: "
        f"{LEADING_LABEL.sub('', first)[:140]!r} cites nothing about this company. "
        f"Open with the observed fact that makes this approach theirs — their "
        f"equipment, product, certification, posting, stack or person — and end "
        f"that sentence with its CLAIM_ID."
    ]


def _company_words(company_names: list[str]) -> set[str]:
    words: set[str] = set()
    for name in company_names:
        words.update(_words(name))
    return words


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def title_nouns(name: str, company_names: list[str], evidence: Evidence) -> list[str]:
    """Words in a title that came from this company's own evidence.

    Compared on a short stem, so "sleever" in a title finds "sleevers" in a grant
    description and "moulded" finds "moulding", without a dictionary.
    """
    theirs = _company_words(company_names)
    stems = {_stem(w) for w in evidence.words}
    found = []
    for word in _words(name):
        if (len(word) < 3 and not word.isdigit()) or word in STOPWORDS:
            continue
        if word in GENERIC_TITLE_WORDS or word in theirs:
            continue
        if _stem(word) in stems or any(w.startswith(word) for w in evidence.words):
            found.append(word)
    return found


def title_failures(
    number: int, name: str, company_names: list[str], evidence: Evidence
) -> list[str]:
    """Rule 2: the title carries a noun from their evidence, not only a pattern."""
    if not evidence.words:
        return []
    if title_nouns(name, company_names, evidence):
        return []
    return [
        f"approach {number} is titled {name!r}, which names the pattern and not the "
        f"company. Put their equipment, product or process in the title, in a "
        f"word taken from their own evidence — not their company name, which "
        f"would make any archetype look bespoke."
    ]


def fallback_title(name: str, company: str) -> str:
    """The honest title when no noun of theirs can be found.

    The offer genuinely is the shared pattern, so the title says so and says for
    whom. It is recorded as a fallback rather than counted as bespoke, and it can
    never collide with another company's, because it carries their name.
    """
    short = re.sub(r"\b(inc|ltd|llc|limited|corp|corporation|co)\.?$", "",
                   company.strip(), flags=re.IGNORECASE).strip(" ,.")
    return f"{name.strip()} — {short}"


# ------------------------------------------------------------------- rule 4

def normalise_title(name: str) -> str:
    return " ".join(_words(name))


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = _words(CITATION.sub(" ", LEADING_LABEL.sub("", text or "")))
    return {tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def shared_content(first: str, second: str) -> float:
    """How much of the shorter scope is also in the longer, 0 to 1.

    Measured on runs of three words rather than on vocabulary. Two scopes for
    two quote assemblers SHOULD share vocabulary — that is the shared pattern —
    and a word-set comparison would call them copies. Runs of three words only
    match where a sentence was reused, and the company's own numbers and nouns,
    left in, break the runs wherever the scope is genuinely theirs.
    """
    left, right = _shingles(first), _shingles(second)
    if len(left) < MIN_SHINGLES or len(right) < MIN_SHINGLES:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def title_collisions(names: list[str], others: dict[str, str]) -> list[str]:
    """Rule 4: an offer title already used for another company is refused."""
    failures = []
    for number, name in enumerate(names, start=1):
        owner = others.get(normalise_title(name))
        if owner:
            failures.append(
                f"approach {number} is titled {name!r}, which is already the title "
                f"of an offer to {owner}. Two companies may share a pattern; they "
                f"may not share a title. Name it after something only this company "
                f"has.")
    return failures


def near_copies(
    proses: list[str], others: list[tuple[str, int, str]]
) -> list[tuple[int, str, int, float]]:
    """Each new scope paragraph that is mostly another company's, with its score."""
    found = []
    for number, prose in enumerate(proses, start=1):
        best = max(((shared_content(prose, other), owner, their_number)
                    for owner, their_number, other in others),
                   default=(0.0, "", 0))
        if best[0] > NEAR_IDENTICAL:
            found.append((number, best[1], best[2], best[0]))
    return found


# ---------------------------------------------------------------- the book

def parse_offers(body: str) -> list[tuple[int, str, str, str]]:
    """(number, name, pitch, prose) for each approach in a stored analysis body."""
    start = body.find("## Three approaches")
    if start < 0:
        return []
    end = body.find("### Lead recommendation", start)
    section = body[start:end if end >= 0 else len(body)]
    out = []
    for match in re.finditer(r"^### (\d)\. (.+)$", section, flags=re.MULTILINE):
        tail = section[match.end():]
        stop = re.search(r"^### ", tail, flags=re.MULTILINE)
        block = tail[:stop.start() if stop else len(tail)].strip()
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", block) if p.strip()]
        if not paragraphs:
            continue
        pitch = paragraphs[0]
        rest = paragraphs[1:]
        if rest and re.match(r"^\d+-\d+ weeks · ", rest[-1]):
            rest = rest[:-1]
        out.append((int(match.group(1)), match.group(2).strip(), pitch, "\n\n".join(rest)))
    return out


def pattern_of(model_id: str | None) -> str | None:
    """The ROI pattern inside a finmodel id: company.pattern.engagement."""
    parts = (model_id or "").split(".")
    return parts[-2] if len(parts) >= 3 else None


def offers_from(
    prospect: dict[str, Any], country: str, analysis: dict[str, Any]
) -> list[Offer]:
    """Every offer in one company's live analysis."""
    models = (analysis.get("gate_map") or {}).get("models") or []
    out = []
    for number, name, pitch, prose in parse_offers(str(analysis.get("body") or "")):
        model = models[number - 1] if 0 < number <= len(models) else {}
        out.append(Offer(
            company_id=prospect["id"], company=str(prospect.get("company_name") or ""),
            country=country, number=number, name=name, pitch=pitch, prose=prose,
            pattern=pattern_of((model or {}).get("model_id"))))
    return out


def book_report(offers: list[Offer], evidence: dict[str, Evidence],
                names: dict[str, list[str]]) -> dict[str, Any]:
    """The differentiation of a book of offers, as numbers an operator can read."""
    by_company: dict[str, list[Offer]] = defaultdict(list)
    for offer in offers:
        by_company[offer.company_id].append(offer)
    leads = [o for o in offers if o.number == 1]

    owners: dict[str, set[str]] = defaultdict(set)
    for offer in offers:
        owners[normalise_title(offer.name)].add(offer.company)
    shared_titles = {t: sorted(c) for t, c in owners.items() if len(c) >= 2}

    def passes(offer: Offer, rule: str) -> bool:
        ev = evidence.get(offer.company_id, Evidence(frozenset(), frozenset()))
        if rule == "binding":
            return not binding_failures(offer.number, offer.prose, ev)
        if rule == "anchor":
            return not anchor_failures(offer.number, offer.prose, ev)
        return not title_failures(offer.number, offer.name,
                                  names.get(offer.company_id, []), ev)

    pairs = []
    best: dict[tuple[str, int], float] = {}
    for first, second in combinations(offers, 2):
        if first.company_id == second.company_id:
            continue
        score = shared_content(first.prose, second.prose)
        for offer in (first, second):
            key = (offer.company_id, offer.number)
            best[key] = max(best.get(key, 0.0), score)
        if score > NEAR_IDENTICAL:
            pairs.append((first.company, first.number, second.company,
                          second.number, round(score, 2)))

    total = len(offers) or 1
    return {
        "companies": len(by_company),
        "offers": len(offers),
        "distinct_lead_titles": len({normalise_title(o.name) for o in leads}),
        "leads": len(leads),
        "titles_shared_across_companies": len(shared_titles),
        "offers_under_a_shared_title": sum(len(c) for c in shared_titles.values()),
        "shared_titles": shared_titles,
        "bespoke_titles": sum(passes(o, "title") for o in offers),
        "bound_scopes": sum(passes(o, "binding") for o in offers),
        "anchored_scopes": sum(passes(o, "anchor") for o in offers),
        "near_identical_pairs": sorted(pairs, key=lambda p: -p[4]),
        "mean_nearest_similarity": round(sum(best.values()) / max(len(best), 1), 3),
        "patterns": Counter(o.pattern for o in offers if o.pattern).most_common(),
        "share_bespoke": round(sum(passes(o, "title") for o in offers) / total, 3),
    }
