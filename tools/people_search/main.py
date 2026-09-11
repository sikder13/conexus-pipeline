"""people_search — record a named human found by searching the open web.

WHY THIS EXISTS SEPARATELY FROM THE people NODE

The `people` node reads the company's own site. It cannot help a company whose
site we never found, and after the Cedar-class repair a large part of the
Canadian set has no site at all. The names are often published anyway: FedDev
Ontario and IRAP write success stories about exactly these companies, and a
government page naming a founder is as good a source as an About page.

WHAT THIS TOOL IS AND IS NOT

It is not a searcher. The search and the reading of the page happen outside it,
by an operator or an agent with a browser, and the FINDINGS are handed here as
a file: who, what role, which URL, and the sentence on that page that says so.
This tool is the part that must not be improvised — it applies the same name
and role rules the node applies, writes claims through `lib/claims.py` like
every other claim, merges rather than replaces, and recomputes the scoring flag
from what survives.

WHAT IT REFUSES

A name that does not parse as a person, a role that is not a real job title, a
source that is not an http URL, and any finding without the sentence it came
from. It never writes `claimcheck` — that verdict belongs to the adversarial
checker, which reads the page itself. This tool records what was found; the
checker decides whether the page really says it, and the subject guard decides
whether the page is even about this company.

    python -m tools.people_search --findings found.json --apply
    python -m tools.claimcheck --adapter canada_gc        # verbatim check
    python -m tools.claimcheck --sweep-people             # subject guard
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from rich.console import Console
from rich.table import Table

from lib import db
from lib.claims import Tier, make_claim
from lib.evidence import BLOCK7_PEOPLE
from lib.integrity import is_killed, is_tainted
from lib.persongate import name_is_a_person, role_is_real, split_person_claim
from tools.harvester.nodes.people import is_decision_role

REQUIRED = ("name", "role", "source_url", "quote")


def problems_with(finding: dict[str, Any], company: str) -> list[str]:
    """Why this finding may not be written, or an empty list."""
    out = []
    for key in REQUIRED:
        if not str(finding.get(key) or "").strip():
            out.append(f"missing {key}")
    name = str(finding.get("name") or "").strip()
    role = str(finding.get("role") or "").strip()
    if name and not name_is_a_person(name, company):
        out.append(f"{name!r} does not parse as a person's name")
    if role and not role_is_real(role):
        out.append(f"{role!r} does not parse as a real job title")
    source = str(finding.get("source_url") or "")
    if source and not source.startswith("http"):
        out.append("source_url is not an http URL")
    tier = finding.get("tier")
    if tier not in (1, 2):
        out.append(f"tier {tier!r} is not 1 or 2 — a person is asserted or not at all")
    quote = str(finding.get("quote") or "")
    if quote and name and name.split()[0].lower() not in quote.lower():
        out.append("the quoted sentence does not contain the name")
    return out


def merge_people(
    existing: list[Any], fresh: list[dict[str, Any]]
) -> tuple[list[Any], int, int]:
    """Add the new people, leaving what is already there alone.

    A quarantined person is kept, marker and all — this is not the place that
    decides a claim was wrong, and overwriting the record of a mistake with a
    correction loses the mistake.

    A second source for a name already on file is written as a SECOND CLAIM,
    not as a field on the first. That is what corroboration is made of:
    `persongate.independent_sources` counts distinct domains across claims, so a
    source recorded as an annotation is a source the gate cannot see. Only an
    exact repeat — same person, same page — is dropped.
    """
    out = list(existing or [])
    added = corroborated = 0
    for claim in fresh:
        name, _role = split_person_claim(claim.get("value"))
        same = [c for c in out
                if isinstance(c, dict) and not is_tainted(c) and not is_killed(c)
                and split_person_claim(c.get("value"))[0].lower() == name.lower()]
        if any(c.get("source_url") == claim["source_url"] for c in same):
            continue
        out.append(claim)
        if same:
            corroborated += 1
        else:
            added += 1
    return out, added, corroborated


def decision_makers(people: list[Any]) -> list[str]:
    """The usable people who hold a role that can say yes."""
    out = []
    for claim in people or []:
        if not isinstance(claim, dict) or is_tainted(claim) or is_killed(claim):
            continue
        name, role = split_person_claim(claim.get("value"))
        if name and is_decision_role(role):
            out.append(name)
    return out


def apply_to(prospect: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    """The patch these findings make to one prospect."""
    evidence = prospect.get("evidence_file") or {}
    block = dict(evidence.get(BLOCK7_PEOPLE) or {})
    fresh = []
    for finding in findings:
        claim = make_claim(
            f"{finding['name'].strip()} — {finding['role'].strip()}",
            Tier(int(finding["tier"])),
            finding["source_url"].strip(),
        )
        # The sentence the page actually carries, stored beside the claim so a
        # reviewer can argue with the reading without re-fetching the page. It
        # is NOT a claimcheck verdict and must never be mistaken for one.
        claim["found_quote"] = str(finding["quote"]).strip()[:400]
        claim["found_by"] = "open-web search, verified against the page"
        fresh.append(claim)

    people, added, corroborated = merge_people(block.get("named_people"), fresh)
    block["named_people"] = people
    names = decision_makers(people)
    flags = dict(block.get("flags") or {})
    source = next((c["source_url"] for c in fresh if c["value"].split(" — ")[0] in names),
                  fresh[0]["source_url"] if fresh else "")
    if names:
        flag = make_claim(True, Tier.T1, source)
        flag["people"] = names
        flag["derivation"] = "our reading of the pages named on each person claim"
        flags["named_decision_maker"] = flag
    block["flags"] = flags
    return {
        "evidence_file": {**evidence, BLOCK7_PEOPLE: block},
        "_added": added,
        "_corroborated": corroborated,
        "_decision_makers": names,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record people found by open-web search as evidence claims.")
    parser.add_argument("--findings", required=True,
                        help="JSON: [{company, people: [{name, role, source_url, "
                             "tier, quote}]}]")
    parser.add_argument("--adapter", default="canada_gc")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    console = Console()

    with open(args.findings) as fh:
        payload = json.load(fh)

    by_name = {}
    for prospect in db.list_prospects_full(args.adapter):
        by_name[(prospect.get("company_name") or "").strip().lower()] = prospect

    table = Table(title="people recovered by search")
    for column in ("company", "person", "role", "tier", "verdict"):
        table.add_column(column, overflow="fold")
    written = refused = 0

    for entry in payload:
        company = str(entry.get("company") or "").strip()
        prospect = by_name.get(company.lower())
        if prospect is None:
            table.add_row(company[:28], "—", "—", "—", "[red]no such company[/red]")
            refused += 1
            continue
        keep = []
        for finding in entry.get("people") or []:
            issues = problems_with(finding, company)
            if issues:
                table.add_row(company[:28], str(finding.get("name"))[:22],
                              str(finding.get("role"))[:18], str(finding.get("tier")),
                              f"[red]{'; '.join(issues)[:60]}[/red]")
                refused += 1
                continue
            keep.append(finding)
            table.add_row(company[:28], finding["name"][:22], finding["role"][:18],
                          f"T{finding['tier']}", "[green]recorded[/green]")
            written += 1
        if not keep:
            continue
        patch = apply_to(prospect, keep)
        summary = {k: patch.pop(k) for k in list(patch) if k.startswith("_")}
        if args.apply:
            db.update_prospect(prospect["id"], patch)
        console.print(
            f"[dim]{company}: +{summary['_added']} new, "
            f"{summary['_corroborated']} corroborated, "
            f"decision-makers: {', '.join(summary['_decision_makers']) or 'none'}[/dim]")

    console.print(table)
    console.print(f"{written} recorded · {refused} refused"
                  + ("" if args.apply else " [yellow](dry run — nothing written)[/yellow]"))
    if args.apply and written:
        console.print("[cyan]next: python -m tools.claimcheck --adapter "
                      f"{args.adapter}  then  --sweep-people[/cyan]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
