"""Publication validation and Git integration."""

from .gates import (
    FrontMatterGate,
    GateContext,
    GateFailure,
    GateResult,
    GitDiffScopeGate,
    HugoBuildGate,
    PublicSourcesGate,
    PublishValidator,
    SecretsGate,
)
from .git import GitPublishResult, GitPublisher
from .service import PublicationResult, PublicationService

__all__ = [
    "FrontMatterGate",
    "GateContext",
    "GateFailure",
    "GateResult",
    "GitDiffScopeGate",
    "GitPublishResult",
    "GitPublisher",
    "HugoBuildGate",
    "PublicationResult",
    "PublicationService",
    "PublicSourcesGate",
    "PublishValidator",
    "SecretsGate",
]
