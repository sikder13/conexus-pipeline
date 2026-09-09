"""Which of the four industries an award is in, read off what the record says.

WHAT IS BEING CLASSIFIED

Not the company — the award. This dataset carries no industry description of the
recipient; what it carries is a programme, a programme purpose, an agreement
title, a project description and a statement of expected results. Together those
say what the money is for, and for a grant that funds a production line that is
usually the same thing as what the company does.

It is not always the same thing, and the classifier says so by keeping the words
that placed each award. A machine shop that took a food-safety grant will be
placed in food processing on the words that did it, and an operator reading the
report can see the placement was made on "HACCP" and disagree with it. A
classification nobody can argue with is a classification nobody can correct.

WHY KEYWORDS AND NOT A MODEL

Because the placement decides whether a company is contacted at all, and a
keyword family is inspectable, deterministic and free. A language model here
would be more accurate and would also make every count in the run report
unreproducible, which is a bad trade for a filter.

THE RECIPIENT'S NAME COUNTS FOR MORE

The four families are matched against the award text and against the recipient's
name, with the name weighted more heavily — the same rule, and the same weight,
that `lib/peers.py` already uses to place Indiana companies. A company's name is
the most deliberate description of itself it will ever write, and without that
weighting a subordinate clause about workplace training outvotes "Precision
Machining Inc." This is an addition to classifying on programme and purpose
alone, not a replacement for it: an award with no family words in its name is
still placed on its text.

NOT PLACED IS NOT A FAMILY

An award matching nothing is `unclassified`, and unclassified awards are
excluded from the list rather than swept into a residual bucket. The count is
reported, because a large one would mean the families are wrong rather than that
the awards are uninteresting.
"""

from __future__ import annotations

import re
from typing import NamedTuple

NAME_WEIGHT = 3
"""How much more a family word in the recipient's name counts than one in the
award text. Matches `lib/peers.py`, and for the same reason."""

MIN_TEXT_WORDS: dict[str, int] = {
    "food_processing": 1,
    "agri_food": 1,
    "manufacturing": 1,
    "distribution": 2,
}
"""How many distinct family words a placement needs when the NAME carries none.

One for three of the families and two for distribution, and the asymmetry is
the point rather than a fudge. "Manufacturing", "winery" and "greenhouse" are
words a record uses about the recipient. Distribution's vocabulary is not:
"warehouse", "freight" and "wholesale" are what half the country's software
companies describe their customers with, and a single appearance placed "APS
Aerospace Corporation" and "Plane Sciences Inc." in distribution on one mention
of a warehouse their product talks to. Requiring two turns that off and costs
the family almost nothing real, because a distributor's record says so more
than once.

A hit in the NAME always stands alone, in every family. A company with
"Trucking" above its door is a trucking company on that evidence."""

UNCLASSIFIED = "unclassified"

FAMILIES: dict[str, tuple[str, ...]] = {
    "food_processing": (
        "food processing", "food manufactur", "food and beverage", "food grade",
        "foods", "food product", "beverage", "brewery", "brewing", "distillery",
        "winery", "vintner", "cidery", "bakery", "baking", "confection",
        "snack food", "meat processing", "abattoir", "poultry processing",
        "dairy processing", "cheese", "creamery", "pasteuri", "bottling",
        "canning", "packaging line", "haccp", "food safety", "plant based protein",
        "protein processing", "ready to eat", "seafood processing", "milling",
        "honey", "maple syrup", "sauce", "condiment", "roastery", "coffee roast",
    ),
    "agri_food": (
        "agri food", "agrifood", "agricultur", "agronom", "farm", "farming",
        "greenhouses", "greenhouse grow", "greenhouse produc", "greenhouse veget",
        "greenhouse operat", "horticultur", "crop", "livestock", "cattle", "swine",
        "poultry", "dairy", "grain", "seed", "orchard", "vineyard", "aquaculture",
        "irrigation", "harvest", "soil health", "on-farm", "producer",
        "grower", "apiary", "maple", "tillage", "manure",
    ),
    "manufacturing": (
        "manufactur", "machining", "machine shop", "fabricat", "tool and die",
        "stamping", "welding", "foundry", "casting", "injection mould",
        "injection mold", "moulding", "molding", "extrusion", "cnc",
        "production line", "assembly line", "shop floor", "plant floor",
        "industrial automation", "factory automation", "process automation",
        "automation system", "automated line", "robotic", "additive manufactur",
        "3d printing", "machine vision", "industrial equipment",
        "metal fabrication", "sheet metal", "precision component",
        "laser cutting", "press brake", "composite material", "coating line",
        "plastics", "die cast", "forging", "equipment manufactur", "machinery",
        "factory", "production facility", "millwork", "cabinetry",
        "steel fabricat", "weldment", "tool and mould", "job shop",
    ),
    "distribution": (
        "wholesale", "wholesaler", "distributor", "distribution centre",
        "distribution center", "warehous", "logistics compan", "logistics provider",
        "third party logistics", "3pl", "freight", "trucking", "haulage",
        "motor carrier", "cross dock", "courier service", "moving and storage",
        "transportation services", "cold chain distribution",
    ),
}
"""Keyword families for the four industries this expansion sells into.

Ordered deliberately. Ties keep the earlier family, so an award that is equally
about food and about manufacturing is food processing — "food manufacturing" is
a food business first — and an agri-food award that also mentions a production
line stays agri-food.

Two rounds of tuning against the real data are worth recording, because both
were mistakes a keyword list makes by default:

* **A word that describes a mention is not a word that describes a business.**
  "supply chain", "fleet" and "inventory management" placed a mining-telemetry
  firm, a coffee roaster and three software companies in distribution, because
  the phrases appear in the boilerplate of half the country's relief-fund
  agreements. They are gone. What is left names the trade — wholesaler,
  warehouse, freight, trucking — rather than a topic the award touches.
* **A family needs the words a company puts in its own name.** "Casa Bonita
  Foods" and "Bear Bait Honey" were unplaced because the families held
  "food processing" but not "foods", and "dairy" but not "honey".
* **A word that a different trade also uses is not evidence.** Bare
  "automation", "tooling", "assembly" and "composite" placed a marketing-video
  company, an HR-software company and a positioning-systems company in
  manufacturing, because software prose is full of automation, tooling and
  assemblies. Each was replaced by the phrase that means the factory floor:
  "industrial automation", "tool and die", "assembly line", "composite
  material".
* **"Greenhouse gas" is not a greenhouse.** The phrase appears in a large share
  of clean-technology descriptions in this dataset and was placing energy
  companies in agri-food, so the family names the building — greenhouses,
  greenhouse growing, greenhouse produce — rather than the word."""

FAMILY_WORDS: dict[str, str] = {
    "manufacturing": "manufacturing",
    "food_processing": "food processing",
    "distribution": "distribution, wholesale and transport services",
    "agri_food": "agri-food",
    UNCLASSIFIED: "no industry family matched the award text",
}
"""How each family is named in the report and in a note an operator reads."""

_NON_WORD = re.compile(r"[^a-z0-9]+")


def _flatten(text: str | None) -> str:
    """Lowercase text with punctuation reduced to spaces, for substring matching."""
    return f" {_NON_WORD.sub(' ', (text or '').lower()).strip()} "


def _contains(haystack: str, word: str) -> bool:
    """True when the flattened text contains ``word`` starting at a word boundary.

    Anchored at the start and open at the end, so a stem matches its whole
    family — "manufactur" finds manufacture, manufacturing and manufacturer —
    while a word cannot be found inside a longer one. Plain substring matching
    put three software companies in food processing because "scanning" contains
    "canning", and it would have read "broadcasting" as metal casting.
    """
    return f" {word}" in haystack


class IndustryPlacement(NamedTuple):
    """The family an award was placed in, and the words that placed it."""

    family: str
    words: str
    matched: tuple[str, ...]
    score: int

    @property
    def classified(self) -> bool:
        return self.family != UNCLASSIFIED

    @property
    def basis(self) -> str:
        if not self.matched:
            return "nothing in the award text matched an industry family"
        shown = ", ".join(f"“{word}”" for word in self.matched[:4])
        return f"placed in {self.words} by {shown} in the government record"


def classify_industry(
    award_text: str | None, recipient_name: str | None = None
) -> IndustryPlacement:
    """Place one award in an industry family, or report that nothing matched.

    A longer keyword counts for more than a shorter one, so "food processing"
    outweighs a stray "food", and a hit in the recipient's name counts for
    NAME_WEIGHT times as much as the same hit in the award text.
    """
    text = _flatten(award_text)
    name = _flatten(recipient_name)

    best_family, best_words, best_score = UNCLASSIFIED, (), 0
    for family, words in FAMILIES.items():
        matched: list[str] = []
        text_only: list[str] = []
        score = 0
        named = False
        for word in words:
            in_name = _contains(name, word)
            in_text = _contains(text, word)
            if not (in_name or in_text):
                continue
            matched.append(word)
            named = named or in_name
            if not in_name:
                text_only.append(word)
            weight = len(word.split()) + 1
            score += weight * (NAME_WEIGHT if in_name else 1)
        if not named and len(text_only) < MIN_TEXT_WORDS.get(family, 1):
            continue
        if score > best_score:
            best_family, best_words, best_score = family, tuple(matched), score

    return IndustryPlacement(
        best_family, FAMILY_WORDS[best_family], best_words, best_score
    )
