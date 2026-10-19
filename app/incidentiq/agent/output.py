from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    cause: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[UUID] = Field(max_length=1000)
    contradicting_evidence_ids: list[UUID] = Field(max_length=1000)
    explanation: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def unique_references(self):
        support, contrary = self.supporting_evidence_ids, self.contradicting_evidence_ids
        if len(set(support)) != len(support) or len(set(contrary)) != len(contrary):
            raise ValueError("Evidence references must be unique within each list")
        if set(support) & set(contrary):
            raise ValueError("An observation cannot both support and contradict one hypothesis")
        return self


class InvestigationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_summary: str = Field(min_length=1, max_length=4000)
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)
    missing_evidence: list[str] = Field(max_length=20)
    recommended_next_steps: list[str] = Field(max_length=5)
    disposition: Literal["likely_cause_found", "insufficient_evidence", "inconclusive"]
