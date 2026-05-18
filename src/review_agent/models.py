from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Issue:
    code: str
    category: str
    severity: str
    message: str
    evidence: str
    rule_ref: str
    hint_for_intern: str


@dataclass
class CriterionScore:
    criterion_id: str
    title: str
    weight: int
    score: float
    rationale: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class RiskSignal:
    signal: str
    evidence: str
    explanation: str


@dataclass
class AnalysisResult:
    criteria: list[CriterionScore]
    mentor_issues: list[Issue]
    intern_tips: list[str]
    ai_risk_signals: list[RiskSignal]
    coverage_summary: dict[str, Any]

