"""Which funding programs put a company on our list, and why those.

RESEARCHED FROM THE DATA, NOT FROM A WEBSITE

Every entry below was chosen by reading the dataset itself. On 2026-09-08 the
Ontario and Alberta rows were reduced to their latest amendment (445,216
agreements), narrowed to business recipients (99,631 agreements) and tallied by
program name. The counts in each rationale are that tally, and the run report
prints the top thirty programs again on every run so the operator can extend
this list from evidence rather than from memory.

WHAT THE TALLY SHOWED, AND WHAT IT CHANGED

Three things worth knowing before reading the entries:

* **IRAP is not one program name.** The National Research Council publishes it
  under at least eight — Contributions to Firms, Innovation Assistance Program,
  AI Assist, Clean Technology, three youth-employment streams — so the entry
  matches the family rather than a string.
* **The regional agencies are better identified by department than by program.**
  FedDev Ontario and PrairiesCan both award chiefly through Regional Economic
  Growth through Innovation, but each also runs a dozen smaller programmes; the
  department is the stable identifier and the program name is not.
* **Digital adoption will contribute almost nothing, and that is a finding.**
  The Canadian Digital Adoption Program is the second-largest program in the
  province by business-recipient count — 16,224 recipients — and every one of
  its Boost Your Business Technology grants falls below the $25,000 floor. The
  entry stays because the operator asked for it and because a future intake
  could sit in the band; the report shows the zero rather than hiding it.

NGen is in the list and matches nothing, which is also a finding: Next
Generation Manufacturing Canada is itself the recipient of the ISED cluster
money and re-grants it to member companies under agreements that are not
themselves proactively disclosed. Its members are therefore not reachable from
this dataset, and the entry records that rather than leaving the gap silent.

TOGGLING AN ENTRY

Each entry carries `enabled`. Turning one off is a one-word change that the
report immediately reflects, which is the point: the whitelist is an operator's
instrument, not a constant.
"""

from __future__ import annotations

import re
from typing import NamedTuple

_DASHES = re.compile(r"[‐-―−]")
_SPACE = re.compile(r"\s+")


def normalise(text: str | None) -> str:
    """Lowercase a program or department name and flatten its punctuation.

    The dataset writes the same programme with an en dash, a hyphen and a
    double space in different quarters, so a comparison that respects those
    differences is a comparison that misses awards.
    """
    flattened = _DASHES.sub("-", (text or "").lower())
    return _SPACE.sub(" ", flattened.replace("’", "'")).strip()


def department_english(department: str | None) -> str:
    """The English half of the dataset's bilingual department field."""
    return (department or "").split("|")[0].strip()


class ProgramEntry(NamedTuple):
    """One whitelisted funding programme or funding department."""

    key: str
    display: str
    program_terms: tuple[str, ...]
    department_terms: tuple[str, ...]
    rationale: str
    enabled: bool = True

    def matches(self, program: str | None, department: str | None) -> bool:
        prog = normalise(program)
        dept = normalise(department_english(department))
        if prog and any(term in prog for term in self.program_terms):
            return True
        return bool(dept and any(term in dept for term in self.department_terms))


WHITELIST: tuple[ProgramEntry, ...] = (
    ProgramEntry(
        key="nrc_irap",
        display="NRC IRAP",
        program_terms=(
            "industrial research assistance program",
            "innovation assistance program",
        ),
        department_terms=(),
        rationale=(
            "13,192 business agreements in Ontario and Alberta across eight "
            "programme names. IRAP funds a company's own R&D and technical "
            "advisers work alongside the firm, so a recipient is a manufacturer "
            "that has already decided to spend on capability."
        ),
    ),
    ProgramEntry(
        key="feddev_ontario",
        display="FedDev Ontario",
        program_terms=("federal economic development",),
        department_terms=("federal economic development agency for southern ontario",),
        rationale=(
            "2,812 business agreements. Matched by department: FedDev awards "
            "through Regional Economic Growth through Innovation, Business "
            "Scale-up and Productivity and the Regional Relief and Recovery "
            "Fund, and the department name is the only stable identifier across "
            "them."
        ),
    ),
    ProgramEntry(
        key="prairies_can",
        display="PrairiesCan (and its predecessor, Western Economic Diversification)",
        program_terms=(),
        department_terms=(
            "prairies economic development canada",
            "western economic diversification canada",
        ),
        rationale=(
            "5,699 business agreements, nearly all Alberta. Western Economic "
            "Diversification became PrairiesCan in 2021 and the historical "
            "records keep the old department name, so both are matched or the "
            "Alberta list loses its pre-2022 half."
        ),
    ),
    ProgramEntry(
        key="regi",
        display="Regional Economic Growth through Innovation",
        program_terms=("regional economic growth through innovation",),
        department_terms=(),
        rationale=(
            "5,729 business agreements — the largest single in-band programme in "
            "the two provinces. Named as well as matched by department because "
            "the same programme is delivered by FedNor in Northern Ontario, and "
            "a department-only rule would drop those recipients."
        ),
    ),
    ProgramEntry(
        key="ngen",
        display="NGen / Global Innovation Clusters",
        program_terms=(
            "next generation manufacturing",
            "ngen",
            "global innovation cluster",
            "supercluster",
        ),
        department_terms=(),
        rationale=(
            "Matches three agreements in the two provinces, and none of them is "
            "a member company: NGen is itself the recipient of the cluster money "
            "and its sub-awards to manufacturers are not proactively disclosed. "
            "Kept so that the gap is visible in the report rather than assumed "
            "away, and so a future disclosure would be picked up."
        ),
    ),
    ProgramEntry(
        key="agri_food_innovation",
        display="Agri-food innovation and processing (AAFC)",
        program_terms=(
            "agriinnovate",
            "agriscience",
            "agricultural clean technology",
            "agrimarketing",
            "agriassurance",
            "emergency processing fund",
            "dairy farm investment program",
            "wine sector support",
            "food waste reduction challenge",
            "agricultural methane reduction challenge",
        ),
        department_terms=(),
        rationale=(
            "1,600 business agreements across ten Agriculture and Agri-Food "
            "Canada programmes that fund capital or process work inside a "
            "business, as against the association and youth-placement "
            "programmes that share the Agri- prefix and fund neither. "
            "AgriMarketing and AgriAssurance are included only through their "
            "SME components, which is what the programme names carry."
        ),
    ),
    ProgramEntry(
        key="digital_adoption",
        display="Canada Digital Adoption Program",
        program_terms=("digital adoption",),
        department_terms=(),
        rationale=(
            "16,224 business recipients in the two provinces and, on the "
            "2026-09-08 data, none inside the $25,000 floor: Boost Your Business "
            "Technology grants are capped near $15,000 and Grow Your Business "
            "Online near $2,400. Kept enabled so the report keeps stating the "
            "zero, which is the honest answer to 'why is digital adoption not on "
            "the list'."
        ),
    ),
)
"""The whitelist, in the order the report prints it."""


class ProgramMatch(NamedTuple):
    """The whitelist entry an award matched, and what it matched on."""

    key: str
    display: str


def enabled_entries() -> tuple[ProgramEntry, ...]:
    return tuple(entry for entry in WHITELIST if entry.enabled)


def match_program(program: str | None, department: str | None) -> ProgramMatch | None:
    """The first enabled whitelist entry this award falls under, or None.

    First rather than best: the entries are ordered, and an award that is both
    an IRAP contribution and a National Research Council award is an IRAP
    contribution. Order is the decision, and it is visible in WHITELIST.
    """
    for entry in enabled_entries():
        if entry.matches(program, department):
            return ProgramMatch(entry.key, entry.display)
    return None


def is_whitelisted(program: str | None, department: str | None) -> bool:
    return match_program(program, department) is not None
