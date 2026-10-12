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
