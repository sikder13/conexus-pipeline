"""The source adapter interface — how a public grant dataset enters the pipeline.

An adapter's only job is to turn one public listing into ``RawProspect`` records
without interpreting them. It reports what the source actually says and leaves
every field the source does not carry set to None. That restraint is the whole
contract: a null here becomes a null in the database and a question for a human,
whereas a guess here becomes a false fact that survives all the way to an email.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class RawProspect(BaseModel):
    """One company as a source describes it, before any interpretation.

    Every populated field must be traceable to ``source_url``. Fields the source
    does not publish stay None — see ``missing_fields`` for what to tell the
    operator about the gap.
    """

    model_config = ConfigDict(frozen=True)

    company_name: str
    source_url: str = Field(description="The page this record was read from.")

    county: str | None = None
    city: str | None = None
    industry_desc: str | None = None
    website: str | None = None

    grant_amount: float | None = None
    grant_round: str | None = None
    grant_year: int | None = None
    tech_purchased: str | None = None
    case_study_url: str | None = None

    def missing_fields(self) -> list[str]:
        """Names of the fields this record has no value for."""
        return sorted(name for name, value in self.model_dump().items() if value is None)


class SourceAdapter(ABC):
    """Extracts prospects from one public dataset."""

    adapter_id: ClassVar[str]

    @abstractmethod
    async def extract(self) -> list[RawProspect]:
        """Fetch the source and return every company it lists.

        Must be safe to call repeatedly: extraction is read-only against the
        source and produces the same records for the same published page.
        """
