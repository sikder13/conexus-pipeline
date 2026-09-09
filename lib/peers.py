"""In-dataset benchmarking — where one company stands among companies like it.

WHY THIS EXISTS, AND WHY IT USES ONLY OUR OWN DATASET

The analysis has to be able to say "three of the eleven comparable companies we
hold put grant money into vision systems; you did not" — because that sentence
is worth more to an operator than any industry average, and unlike an industry
average we can show our working for it. Every company in the comparison is a
row in this database, gathered the same way, so the reader can go and open any
of them.

That constraint is the point. A benchmark drawn from an outside report is a
number we cannot defend, cannot date, and cannot let a prospect correct. A
benchmark drawn from the dataset is a statement about a group we can name.

WHAT A COMPARISON MAY REST ON

Only evidence already recorded, and never an aggregator's guess. Company size
is used to BUILD the group — companies are compared against others of roughly
their scale — but is never itself ranked, because the size figures we hold are
often somebody's estimate and ranking on an estimate manufactures a finding out
of a guess. Every dimension carries the tier of the weakest evidence it read,
and a dimension that can only be answered from an estimate reports itself as
not comparable rather than producing a position.

Positions are written in plain words with the basis attached — "one of three of
eleven", not a percentile — because a percentile hides how many companies it
was computed over, and the honest answer here is usually "over eleven".

SMALL GROUPS

Under four comparable companies, a position is noise. The group widens instead —
first to any size in the same industry family, then to the neighbouring
families, then to every manufacturer in the dataset — and the widening is
carried in the caveat so a reader always knows which group they are being shown.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from lib.claims import Tier
from lib.evidence import (
    BLOCK1_WHAT_THEY_MAKE,
    BLOCK2_GRANT_FUNDED,
    BLOCK3_HIRING_SIGNALS,
    BLOCK4_DIGITAL_FRONT_DOOR,
)

MIN_GROUP = 4
"""Below this many peers a position is noise, and the group widens instead."""

RANKABLE_TIERS = (int(Tier.T1), int(Tier.T2))
"""Tiers a comparison may rest on. Aggregator estimates are excluded by rule:
CLAUDE.md rule 6 says they are for internal filtering, and a ranking published
into an analysis is not filtering."""


# ------------------------------------------------------------ industry families

FAMILIES: dict[str, tuple[str, ...]] = {
    "metal_fabrication": (
        "tool and die", "tool & die", "machining", "machine shop", "stamping",
        "fabrication", "fabricator", "welding", "cnc", "grinding", "tooling",
        "die", "mold", "mould", "sheet metal", "metal forming", "forging",
        "casting", "foundry", "screw machine", "laser cutting",
    ),
    "plastics_rubber": (
        "injection molding", "rotational molding", "thermoform", "plastics",
        "plastic", "rubber", "extrusion", "composite", "resin", "polymer",
        "blow molding", "twin sheet",
    ),
    "food_beverage": (
        "food", "brewery", "brewing", "distillery", "spirits", "bakery",
        "dairy", "beverage", "peanut butter", "ingredients", "nutrition",
        "snack", "co-pack", "copack", "meat", "produce", "confection",
    ),
    "medical_device": (
        "medical", "surgical", "implant", "orthopedic", "orthopaedic",
        "dental", "prosthe", "biomedical", "diagnostic device",
    ),
    "aerospace_defense": (
        "aerospace", "defense", "defence", "aviation", "aircraft", "military",
        "avionics", "space",
    ),
    "automotive": (
        "automotive", "vehicle", "truck", "trailer", "powertrain", "chassis",
        "tier 1 supplier", "oem supplier", "recreational vehicle",
    ),
    "electronics": (
        "electronic", "circuit", "sensor", "semiconductor", "instrumentation",
        "pcb", "wire harness", "connector", "telecommunication", "antenna",
        "network analyzer",
    ),
    "wood_furniture": (
        "wood", "millwork", "furniture", "cabinet", "lumber", "carv",
        "upholster", "casework", "closet", "storage solution", "shelving",
    ),
    "building_products": (
        "concrete", "facade", "roofing", "window", "door manufactur",
        "building product", "insulation", "masonry", "precast",
    ),
    "chemicals_coatings": (
        "coating", "chemical", "adhesive", "paint", "metal finishing",
        "plating", "lubricant", "additive", "anodiz", "powder coat",
    ),
    "machinery_equipment": (
        "machinery", "equipment manufactur", "crane", "conveyor",
        "custom automation", "material handling", "hydraulic", "pump",
        "compressor", "industrial equipment", "machine builder",
        "automation system",
    ),
    "agriculture": (
        "agricultur", "farm", "drainage", "seed", "grain", "livestock",
        "tillage", "irrigation", "ditcher",
    ),
    "printing_packaging": (
        "printing", "label", "packaging", "corrugated", "carton", "flexo",
        "signage", "decal",
    ),
    "textiles_apparel": (
        "textile", "apparel", "fabric", "sewing", "garment", "upholstery fabric",
    ),
    "lab_services": (
        "laboratory", "testing services", "oil analysis", "calibration",
        "inspection services", "analytical",
    ),
}
"""Keyword families over the grant listing's own industry description.

Deliberately overlapping and deliberately blunt. A company is placed in the
family whose words appear most often in its description, and the words that
placed it travel with the group so a reader can disagree with the placement
rather than having to trust it."""

NEIGHBOURS: dict[str, tuple[str, ...]] = {
    "metal_fabrication": ("machinery_equipment", "automotive", "aerospace_defense"),
    "plastics_rubber": ("metal_fabrication", "automotive", "printing_packaging"),
    "food_beverage": ("printing_packaging", "agriculture"),
    "medical_device": ("metal_fabrication", "plastics_rubber", "electronics"),
    "aerospace_defense": ("metal_fabrication", "electronics", "machinery_equipment"),
    "automotive": ("metal_fabrication", "plastics_rubber", "machinery_equipment"),
    "electronics": ("medical_device", "aerospace_defense", "machinery_equipment"),
    "wood_furniture": ("building_products", "printing_packaging"),
    "building_products": ("wood_furniture", "chemicals_coatings", "metal_fabrication"),
    "chemicals_coatings": ("plastics_rubber", "building_products", "metal_fabrication"),
    "machinery_equipment": ("metal_fabrication", "agriculture", "automotive"),
    "agriculture": ("machinery_equipment", "food_beverage"),
    "printing_packaging": ("plastics_rubber", "food_beverage", "wood_furniture"),
    "textiles_apparel": ("plastics_rubber", "wood_furniture"),
    "lab_services": ("electronics", "chemicals_coatings", "medical_device"),
}
"""Which families to reach into when a group is too small to say anything.

Adjacency is about the shape of the operation, not the product: a machine shop
and a machinery builder run comparable workflows, so a front-door or automation
comparison across them still means something. Widening this far is always
reported."""

FAMILY_WORDS: dict[str, str] = {
    "metal_fabrication": "metal fabrication and machining",
    "plastics_rubber": "plastics and rubber",
    "food_beverage": "food and beverage",
    "medical_device": "medical devices",
    "aerospace_defense": "aerospace and defence",
    "automotive": "automotive supply",
    "electronics": "electronics and instrumentation",
    "wood_furniture": "wood, millwork and furniture",
    "building_products": "building products",
    "chemicals_coatings": "chemicals, coatings and finishing",
    "machinery_equipment": "machinery and equipment",
    "agriculture": "agricultural equipment",
    "printing_packaging": "printing and packaging",
    "textiles_apparel": "textiles and apparel",
    "lab_services": "laboratory and testing services",
    "unclassified": "manufacturers we could not place by description",
}
"""How each family is named in prose the operator reads."""


# ----------------------------------------------------------------- size bands

BANDS: tuple[tuple[str, int, int, str], ...] = (
    ("micro", 0, 20, "under 20 people"),
    ("small", 21, 50, "20 to 50 people"),
    ("mid", 51, 100, "50 to 100 people"),
    ("upper_mid", 101, 250, "100 to 250 people"),
    ("large", 251, 10**9, "over 250 people"),
)

UNKNOWN_BAND = ("unknown", "no headcount recorded")


class Size(NamedTuple):
    """A company's headcount, the band it falls in, and how well sourced it is."""

    headcount: int | None
    band: str
    words: str
    tier: int | None
    basis: str

    @property
    def rankable(self) -> bool:
        """Whether this figure is strong enough to compare on rather than group by."""
        return self.tier in RANKABLE_TIERS


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    digits = re.findall(r"\d+", str(value).replace(",", ""))
    return int(digits[0]) if digits else None


def size_of(prospect: dict[str, Any]) -> Size:
    """Best-sourced headcount we hold, and the band it puts the company in.

    Preference order is by strength of source, not by convenience: a count the
    company published beats a count the press reported, which beats an
    aggregator's range. The tier travels with the answer because the band may be
    used to build a group even when the figure is too weak to rank on.
    """
    evidence = prospect.get("evidence_file") or {}
    block8 = evidence.get("block8_financial_scale") or {}
    for key, basis in (("employee_count", "a headcount they publish themselves"),
                       ("company_size", "a headcount reported in press coverage")):
        claim = block8.get(key)
        if isinstance(claim, dict) and (count := _int_or_none(claim.get("value"))):
            return Size(count, *_band_for(count), claim.get("tier"), basis)

    count = _int_or_none(prospect.get("employee_estimate"))
    if count is not None:
        source = str(prospect.get("employee_source") or "")
        tier = int(Tier.T1) if "[T1]" in source else (
            int(Tier.T2) if "[T2]" in source else int(Tier.T3))
        basis = ("a headcount they publish themselves" if tier == int(Tier.T1)
                 else "a headcount reported in press coverage" if tier == int(Tier.T2)
                 else "a directory's estimate, which we group by but never rank on")
        return Size(count, *_band_for(count), tier, basis)

    return Size(None, UNKNOWN_BAND[0], UNKNOWN_BAND[1], None, "no headcount recorded")


def _band_for(count: int) -> tuple[str, str]:
    for name, low, high, words in BANDS:
        if low <= count <= high:
            return name, words
    return UNKNOWN_BAND


# -------------------------------------------------------------- family placing

NAME_WEIGHT = 3
"""How much more a keyword in the company's NAME counts than one in prose.

Hoosier Crane Service Company was placed in laboratory services, because its
grant description mentions the training and testing services it also sells and
that phrase is longer than the word "crane". A company's name is the most
deliberate description of itself it will ever write, and weighting it above the
prose is what stops a subordinate clause outvoting it."""


def _description(prospect: dict[str, Any]) -> tuple[str, str]:
    """What we may read to place a company: its name, and everything else."""
    evidence = prospect.get("evidence_file") or {}
    block1 = evidence.get(BLOCK1_WHAT_THEY_MAKE) or {}
    parts = [
        str(prospect.get("industry_desc") or ""),
        str(prospect.get("tech_purchased") or ""),
    ]
    for key in ("self_description", "business_model_basis", "who_they_sell_to"):
        claim = block1.get(key)
        if isinstance(claim, dict):
            parts.append(str(claim.get("value") or ""))
    return (str(prospect.get("company_name") or "").lower(),
            " ".join(parts).lower())


class Family(NamedTuple):
    """Which industry family a company was placed in, and on what words."""

    key: str
    words: str
    matched: tuple[str, ...]

    @property
    def basis(self) -> str:
        if not self.matched:
            return ("nothing in the grant listing's description matched a known "
                    "industry family")
        shown = ", ".join(f"“{word}”" for word in self.matched[:3])
        return f"placed by {shown} in the grant listing's own description"


def family_of(prospect: dict[str, Any]) -> Family:
    """The industry family whose words appear most often in what we hold.

    Ties keep the earlier family in FAMILIES rather than picking arbitrarily, so
    the same company lands in the same group on every run. A placement is never
    silent: the words that made it travel with the group.
    """
    name, body = _description(prospect)
    best_key, best_hits = "unclassified", ()
    best_score = 0
    for key, words in FAMILIES.items():
        hits = tuple(word for word in words if word in name or word in body)
        # Longer keywords are more specific and count for more, so "tool and die"
        # outweighs a stray "die" inside "diesel".
        score = sum(
            (len(word.split()) + 1) * (NAME_WEIGHT if word in name else 1)
            for word in hits
        )
        if score > best_score:
            best_key, best_hits, best_score = key, hits, score
    return Family(best_key, FAMILY_WORDS[best_key], best_hits)


# ------------------------------------------------------------------ the group

class PeerGroup(NamedTuple):
    """The companies one prospect is being compared against, and why these."""

    subject: dict[str, Any]
    family: Family
    size: Size
    members: list[dict[str, Any]]
    widened: str
    caveat: str

    @property
    def size_of_group(self) -> int:
        return len(self.members)

    @property
    def source_adapter(self) -> str:
        """Which dataset every company in this group came from — always one."""
        return str(self.subject.get("source_adapter") or "")

    @property
    def basis(self) -> str:
        """One sentence naming the group, for the top of the peer table."""
        where = f"across {REGION_WORDS.get(self.source_adapter, 'the')} grant recipients we hold"
        if self.widened == "family_and_size":
            return (f"{self.size_of_group} companies in {self.family.words} at "
                    f"{self.size.words}, {where}")
        if self.widened == "family":
            return f"{self.size_of_group} companies in {self.family.words}, {where}"
        if self.widened == "neighbours":
            return (f"{self.size_of_group} companies in {self.family.words} and "
                    f"related industries, {where}")
        return f"{self.size_of_group} companies {where}"


REGION_WORDS: dict[str, str] = {
    "conexus_iedc": "Indiana",
    "canada_gc": "Ontario and Alberta",
}
"""How each source's territory is named in the sentence that introduces a group.

The sentence used to say "Indiana" unconditionally, which was true of every
company in the database on the day it was written and became a false statement
about a group the moment the second source landed."""


WIDENING_CAVEAT = {
    "family_and_size": "",
    "family": (
        "Too few companies of a comparable size to say anything, so the "
        "comparison is against the whole industry family regardless of size. "
        "Read a difference here as a difference in industry practice, not "
        "necessarily in scale."
    ),
    "neighbours": (
        "Too few companies in the same industry family, so the comparison "
        "reaches into related industries. It is a weaker comparison and a small "
        "gap should not be read as meaningful."
    ),
    "all": (
        "Too few comparable companies at any narrower definition, so this is "
        "measured against every manufacturer in the dataset. Treat it as "
        "orientation rather than as a finding."
    ),
}


def _same_naics(subject: dict[str, Any], candidate: dict[str, Any]) -> bool | None:
    """Whether two companies share an industry code, or None when we hold none.

    Returns None far more often than not: the code is unpopulated across the
    dataset. It narrows a group when it is there and is silently skipped when it
    is not, rather than emptying every group by being absent."""
    first = str(subject.get("naics_guess") or "").strip()
    second = str(candidate.get("naics_guess") or "").strip()
    if not first or not second:
        return None
    return first[:3] == second[:3]


def peer_group(
    subject: dict[str, Any], universe: list[dict[str, Any]]
) -> PeerGroup:
    """The comparable companies for one prospect, widening until there are enough.

    Widening is a ladder, not a search: same family and size, then family, then
    neighbouring families, then everyone. It stops at the first rung with enough
    companies on it, and the rung it stopped at is reported.
    """
    family = family_of(subject)
    size = size_of(subject)
    # Peers come from the same source or from nowhere. A benchmark's whole claim
    # is that every company in it was gathered the same way, and two sources are
    # not the same way: an Indiana company's evidence has a drive time and a
    # Conexus case study, a Canadian one has neither, so "three of eleven
    # comparable companies publish a certification" would be measuring which
    # dataset a company came from and reporting it as a difference in practice.
    others = [
        p for p in universe
        if p.get("id") != subject.get("id")
        and p.get("source_adapter") == subject.get("source_adapter")
    ]

    def in_family(candidate: dict[str, Any], keys: tuple[str, ...]) -> bool:
        if family_of(candidate).key not in keys:
            return False
        shared = _same_naics(subject, candidate)
        return shared is not False

    rungs: list[tuple[str, list[dict[str, Any]]]] = []
    if size.band != UNKNOWN_BAND[0]:
        rungs.append(("family_and_size", [
            p for p in others
            if in_family(p, (family.key,)) and size_of(p).band == size.band
        ]))
    rungs.append(("family", [p for p in others if in_family(p, (family.key,))]))
    neighbours = (family.key, *NEIGHBOURS.get(family.key, ()))
    rungs.append(("neighbours", [p for p in others if in_family(p, neighbours)]))
    rungs.append(("all", others))

    for rung, members in rungs:
        if len(members) >= MIN_GROUP:
            return PeerGroup(subject, family, size, members, rung,
                             _caveat_for(rung, family))
    rung, members = rungs[-1]
    return PeerGroup(subject, family, size, members, rung, _caveat_for(rung, family))


def _caveat_for(rung: str, family: Family) -> str:
    """What a reader has to know about the group before reading a position.

    A company we could not place is grouped with the other companies we could
    not place, and that is a residue rather than a peer group: it is large
    enough to pass the size check and means nothing, which is the worst
    combination a benchmark can have. It says so instead.
    """
    if family.key == "unclassified":
        return (
            "We could not work out what this company makes from the grant "
            "listing, so it is being compared against the other companies we "
            "could not place. That is not an industry group and no position "
            "below should be read as one — establish what they actually do "
            "first."
        )
    return WIDENING_CAVEAT[rung]


# ------------------------------------------------------------------ dimensions

class Position(NamedTuple):
    """Where the subject stands on one dimension, in words a person would use."""

    key: str
    label: str
    subject_value: str
    headline: str
    basis: str
    comparable: bool
    peers_measured: int

    def as_row(self) -> tuple[str, str, str]:
        """The three columns of the peer table."""
        return (self.label, self.subject_value, self.headline)


def _claim(prospect: dict[str, Any], block: str, key: str) -> dict[str, Any]:
    value = ((prospect.get("evidence_file") or {}).get(block) or {}).get(key)
    return value if isinstance(value, dict) else {}


def _flag(prospect: dict[str, Any], block: str, key: str) -> dict[str, Any]:
    flags = ((prospect.get("evidence_file") or {}).get(block) or {}).get("flags") or {}
    value = flags.get(key)
    return value if isinstance(value, dict) else {}


FRONT_DOOR_CHECKS: tuple[tuple[str, bool], ...] = (
    ("ssl_valid", True),
    ("mobile_viewport", True),
    ("has_contact_form", True),
    ("phone_present", True),
    ("address_present", True),
)
"""Front-door signals read as pass or fail. All are things a node saw on the
page itself, which is why this dimension is comparable at all."""


def front_door_score(prospect: dict[str, Any]) -> tuple[int, int] | None:
    """How many front-door checks a company passes, out of how many were run."""
    block = (prospect.get("evidence_file") or {}).get(BLOCK4_DIGITAL_FRONT_DOOR) or {}
    if not block:
        return None
    passed = total = 0
    for key, want in FRONT_DOOR_CHECKS:
        claim = block.get(key)
        if not isinstance(claim, dict) or claim.get("value") is None:
            continue
        total += 1
        passed += int(bool(claim.get("value")) is want)
    broken = _int_or_none(_claim(prospect, BLOCK4_DIGITAL_FRONT_DOOR,
                                 "broken_internal_links").get("value"))
    if broken is not None:
        total += 1
        passed += int(broken == 0)
    return (passed, total) if total else None


DATA_GENERATING = (
    "vision", "sensor", "robot", "monitor", "measur", "inspection", "software",
    "control system", "scada", "plc", "data", "analytic", "camera", "scanner",
    "automated inspection", "cobot", "machine learning", "erp", "mes",
)
"""Words in a grant purchase that mean the equipment produces readings, not just
motion. This is the distinction the whole automation-posture dimension turns on:
a machine that moves faster leaves no trace, and a machine that measures does."""

AUTOMATION_WORDS = (
    "automat", "robot", "cnc", "cobot", "conveyor", "high-speed", "line",
)


def automation_posture(prospect: dict[str, Any]) -> tuple[str, str] | None:
    """What a company's grant purchase says about how far automation has gone.

    Three answers, and the difference between the first two is the one worth
    money: equipment that emits readings creates something to build reporting
    on, equipment that only moves does not.
    """
    text = " ".join([
        str(prospect.get("tech_purchased") or ""),
        str(_claim(prospect, BLOCK2_GRANT_FUNDED, "what_the_grant_funded").get("value") or ""),
        str(_claim(prospect, BLOCK2_GRANT_FUNDED, "tech_purchased").get("value") or ""),
    ]).lower().strip()
    if not text:
        return None
    if any(word in text for word in DATA_GENERATING):
        return ("data_generating", "bought equipment that produces readings")
    if any(word in text for word in AUTOMATION_WORDS):
        return ("mechanical", "bought equipment that moves work, not that measures it")
    return ("other", "the purchase names no automation")


def open_roles(prospect: dict[str, Any]) -> int | None:
    """How many open roles a careers page showed when it was read."""
    return _int_or_none(
        _claim(prospect, BLOCK3_HIRING_SIGNALS, "open_roles_found").get("value"))


def certifications(prospect: dict[str, Any]) -> list[str]:
    """Quality certifications a company publishes. Their own words, so assertable."""
    block = (prospect.get("evidence_file") or {}).get(BLOCK1_WHAT_THEY_MAKE) or {}
    listed = block.get("certifications")
    if not isinstance(listed, list):
        return []
    return [str(c.get("value")) for c in listed
            if isinstance(c, dict) and c.get("value")
            and c.get("tier") in RANKABLE_TIERS]


def grant_capital(prospect: dict[str, Any]) -> int | None:
    """Capital the grant provably put to work — the award plus the matching money.

    The programme requires a one-to-one match, so an award is a floor on what the
    company itself committed. That doubling is the single strongest financial
    figure in the file and it is a record, not an estimate.
    """
    amount = prospect.get("grant_amount")
    if amount is None:
        amount = _int_or_none(
            _claim(prospect, BLOCK2_GRANT_FUNDED, "grant_amount").get("value"))
    if not amount:
        return None
    return int(float(amount) * 2)


def _count_words(count: int) -> str:
    """Small counts as words, for sentences that read aloud."""
    words = {0: "no", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
             6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    return words.get(count, str(count))


def _share_headline(subject_has: bool, shared: int, measured: int, trait: str) -> str:
    """'one of 3 of 11 with X' — the shape every share position takes.

    Digits for the counts on purpose. The reader is being asked to weigh three
    against eleven, and spelling both out makes that arithmetic harder to do at
    a glance, which is the opposite of what the sentence is for.
    """
    if shared == 0:
        return (f"nor is any of the {measured} we could measure"
                if not subject_has else
                f"the only one of {measured + 1} we could measure {trait}")
    if subject_has and shared >= measured:
        return f"as did every one of the {measured} we could measure"
    if subject_has:
        return f"one of {shared} of {measured} {trait}"
    if shared == 1:
        return f"not the one of {measured} {trait}"
    return f"not among the {shared} of {measured} {trait}"


def _rank_headline(better: int, measured: int) -> str:
    """Where a ranked figure sits, said as a count rather than a percentile."""
    if better == 0:
        return f"ahead of every one of the {measured} we could measure"
    if better >= measured:
        return f"behind all {measured} we could measure"
    if better == 1:
        return f"behind one of the {measured} we could measure, ahead of the rest"
    return f"{better} of the {measured} we could measure are ahead of them"


def compare(group: PeerGroup) -> list[Position]:
    """Where the subject stands on every dimension the evidence can answer.

    A dimension the subject has no evidence for is still reported — as a gap,
    with how many peers do have it — because "eight of eleven publish a
    certification and you publish none we can see" is a finding, and dropping
    the row would hide it.
    """
    subject, members = group.subject, group.members
    out: list[Position] = []

    # 1 — front door.
    mine = front_door_score(subject)
    scored = [(p, s) for p in members if (s := front_door_score(p))]
    if mine and scored:
        my_share = mine[0] / mine[1]
        better = sum(1 for _p, (passed, total) in scored if passed / total > my_share)
        out.append(Position(
            "front_door", "Front door",
            f"passes {mine[0]} of {mine[1]} checks on their own site",
            _rank_headline(better, len(scored)),
            "read from their site and each peer's site directly",
            True, len(scored)))
    else:
        out.append(Position(
            "front_door", "Front door",
            "their site was not readable when we looked",
            "not compared — we hold no reading of their front door",
            "no front-door assessment on file", False, len(scored)))

    # 2 — automation posture, from what the grant actually bought.
    posture = automation_posture(subject)
    measured = [(p, a) for p in members if (a := automation_posture(p))]
    generating = sum(1 for _p, (kind, _w) in measured if kind == "data_generating")
    if posture and measured:
        out.append(Position(
            "automation", "Automation posture", posture[1],
            _share_headline(posture[0] == "data_generating", generating, len(measured),
                            "whose grant bought equipment that produces readings"),
            "the grant record's own description of what was purchased",
            True, len(measured)))
    else:
        out.append(Position(
            "automation", "Automation posture",
            "the grant record does not say what was bought",
            (f"{generating} of {len(measured)} bought equipment that produces "
             f"readings; we cannot place this company against them")
            if measured else "not compared — no purchase descriptions in the group",
            "the grant record's own description of what was purchased",
            False, len(measured)))

    # 3 — hiring.
    roles = open_roles(subject)
    counted = [(p, r) for p in members if (r := open_roles(p)) is not None]
    hiring = sum(1 for _p, r in counted if r > 0)
    if roles is not None and counted:
        out.append(Position(
            "hiring", "Hiring",
            (f"{_count_words(roles)} open role{'' if roles == 1 else 's'} on their "
             f"careers page when we read it"),
            _share_headline(roles > 0, hiring, len(counted), "advertising openly"),
            "each company's own careers page, read directly", True, len(counted)))
    else:
        out.append(Position(
            "hiring", "Hiring", "no careers page we could read",
            (f"{hiring} of {len(counted)} were advertising when we looked")
            if counted else "not compared — no careers pages in the group",
            "each company's own careers page, read directly", False, len(counted)))

    # 4 — certifications.
    mine_certs = certifications(subject)
    with_certs = [p for p in members if certifications(p)]
    known = [p for p in members
             if (p.get("evidence_file") or {}).get(BLOCK1_WHAT_THEY_MAKE)]
    out.append(Position(
        "certifications", "Certifications",
        ", ".join(mine_certs) if mine_certs else "none published that we found",
        _share_headline(bool(mine_certs), len(with_certs), len(known),
                        "publishing a quality certification"),
        "each company's own site, in their words", bool(known), len(known)))

    # 5 — capital deployed through the grant.
    mine_capital = grant_capital(subject)
    capitals = [(p, c) for p in members if (c := grant_capital(p))]
    if mine_capital and capitals:
        richer = sum(1 for _p, c in capitals if c > mine_capital)
        out.append(Position(
            "grant_capital", "Capital deployed",
            f"about ${mine_capital:,} counting the money they had to match",
            _rank_headline(richer, len(capitals)),
            "the grant programme's own award records, doubled for the required "
            "matching money", True, len(capitals)))
    else:
        out.append(Position(
            "grant_capital", "Capital deployed",
            "no award figure published for them",
            (f"{len(capitals)} of the group have a published award")
            if capitals else "not compared — no award figures in the group",
            "the grant programme's own award records", False, len(capitals)))

    return out


# -------------------------------------------------------------------- rendering

def table_rows(positions: list[Position]) -> list[tuple[str, str, str]]:
    """The peer table as plain rows: dimension, them, where that puts them."""
    return [p.as_row() for p in positions]


def as_prompt_block(group: PeerGroup, positions: list[Position]) -> str:
    """The peer table as prompt text.

    The generator is handed conclusions, not a dataset to draw its own from. It
    may reason about what a position MEANS commercially and may not compute a
    new one — every comparative sentence it writes has to be traceable to a row
    here, and giving it only the rows is what makes that checkable.
    """
    lines = [
        "PEER COMPARISON. These positions are computed from our own dataset and "
        "are the ONLY comparative statements you may make. Do not invent a "
        "percentile, an industry average, or a peer figure that is not below.",
        f"The group: {group.basis}.",
        f"How the group was built: {group.family.basis}.",
    ]
    if group.caveat:
        lines.append(f"CARRY THIS CAVEAT INTO THE TEXT: {group.caveat}")
    lines.append("")
    for position in positions:
        lines.append(
            f"- {position.label}: they {position.subject_value}. "
            f"Against the group: {position.headline}. "
            f"Basis: {position.basis}."
            + ("" if position.comparable else " NOT COMPARABLE — say so plainly.")
        )
    return "\n".join(lines)


def summarise(group: PeerGroup, positions: list[Position]) -> dict[str, Any]:
    """The peer read as plain data, for storage and for the console."""
    return {
        "family": group.family.key,
        "family_words": group.family.words,
        "matched_words": list(group.family.matched),
        "size_band": group.size.band,
        "size_words": group.size.words,
        "size_rankable": group.size.rankable,
        "group_size": group.size_of_group,
        "widened": group.widened,
        "caveat": group.caveat,
        "basis": group.basis,
        "positions": [p._asdict() for p in positions],
    }
