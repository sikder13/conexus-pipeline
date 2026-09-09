"""canada_gc — the Government of Canada Grants & Contributions adapter.

One module per decision, so that each can be reviewed and changed on its own:

* `dataset`     where the file is, what its columns mean, how to read it
* `recipients`  which recipients are businesses, and why each exclusion fires
* `programs`    which funding programmes put a company on the list
* `industries`  which of the four industries an award is in
* `filters`     the funnel that composes them, and the account of every drop
* `adapter`     the run: fetch, stream, group into companies, snapshot
"""

from lib.sources.canada_gc.adapter import CanadaGCAdapter, Extraction, as_raw_prospect
from lib.sources.canada_gc.filters import CanadaRecipient, FilterSettings

__all__ = [
    "CanadaGCAdapter",
    "CanadaRecipient",
    "Extraction",
    "FilterSettings",
    "as_raw_prospect",
]
