"""Harvester nodes.

Importing this package registers every node it contains, which is what makes
them visible to the runner. A node that is never imported is a node that never
runs, so new modules must be imported here.

The import order below is alphabetical, not dependency order — the runner sorts
by declared dependencies at run time, so nothing here needs to know that
job_postings waits on front_door.
"""

from tools.harvester.nodes import (  # noqa: F401  (importing registers each node)
    case_study,
    front_door,
    grant_news,
    identity,
    job_postings,
    people,
    score,
    summary,
    website,
)
