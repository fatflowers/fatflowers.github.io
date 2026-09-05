"""Structured analysis contracts."""

from .schema import AnalysisEvidence, AnalysisResult, AnalysisValidationError, validate_analysis
from .correlation import CorrelationCandidate, CorrelationEvent, correlate_events

__all__ = [
    "AnalysisEvidence",
    "AnalysisResult",
    "AnalysisValidationError",
    "CorrelationCandidate",
    "CorrelationEvent",
    "correlate_events",
    "validate_analysis",
]
