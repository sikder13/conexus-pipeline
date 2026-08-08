"""Harvester nodes.

Importing this package registers every node it contains, which is what makes
them visible to the runner. A node that is never imported is a node that never
runs, so new modules must be imported here.
"""

from tools.harvester.nodes import identity, website  # noqa: F401  (import registers)
