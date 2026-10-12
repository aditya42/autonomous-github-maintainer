from typing import Literal
from pydantic import BaseModel, Field


class Plan(BaseModel):
    action: Literal["IMPLEMENT", "SKIP"]
    title: str
    rationale: str
    category: Literal["test", "bugfix", "docs", "refactor", "ci", "reliability"]
    target_files: list[str]
    meaningfulness: float = Field(ge=0, le=1)
    risk: Literal["low", "medium", "high"]
    # Defaults preserve older tests; the planning prompt requires explicit values.
    defect_risk: float = Field(default=0, ge=0, le=1)
    regression_prevention: float = Field(default=0, ge=0, le=1)
    impact: float = Field(default=0, ge=0, le=1)
    maintainability: float = Field(default=0, ge=0, le=1)
    evidence_id: str = ""
    evidence_file: str = ""
    evidence_quote: str = ""
    missing_behavior: str = ""


class Edit(BaseModel):
    path: str
    content: str


class Implementation(BaseModel):
    explanation: str
    edits: list[Edit]


class Review(BaseModel):
    approved: bool
    meaningful: bool
    reason: str
    score: float = Field(ge=0, le=1)
