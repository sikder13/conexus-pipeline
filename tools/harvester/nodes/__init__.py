"""Harvester nodes.

Importing this package registers every node it contains, which is what makes
them visible to the runner. A node that is never imported is a node that never
runs, so new modules must be imported here.

The import order below is alphabetical, not dependency order — the runner sorts
by declared dependencies at run time, so nothing here needs to know that
job_postings waits on front_door.
"""

from tools.harvester.nodes import (  # noqa: F401  (importing registers each node)
    canada_news,
    case_study,
    competitor_scan,
    contact_discovery,
    corroborate,
    front_door,
    grant_news,
    headcount_harvest,
    identity,
    job_postings,
    people,
    rival_scan,
    score,
    summary,
    website,
)
