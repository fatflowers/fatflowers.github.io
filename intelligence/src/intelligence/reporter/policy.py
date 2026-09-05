from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from intelligence.models.runs import RunStatus

from .models import ReportEdition, ReportSignal


@dataclass(frozen=True)
class PolicyDecision:
    should_generate: bool
    selected: tuple[ReportSignal, ...]
    deferred: tuple[ReportSignal, ...]
    reason: str
    run_status: RunStatus


class ReportPolicy:
    """Deterministic daily and weekly report selection policy."""

    def __init__(self, *, midday_min_importance: int = 4, low_priority_max: int = 2):
        if not 1 <= midday_min_importance <= 5:
            raise ValueError("midday_min_importance must be between 1 and 5")
        self.midday_min_importance = midday_min_importance
        self.low_priority_max = low_priority_max

    def decide(
        self,
        edition: ReportEdition,
        signals: Union[tuple[ReportSignal, ...], list[ReportSignal]],
    ) -> PolicyDecision:
        ordered = tuple(
            sorted(
                signals,
                key=lambda signal: (
                    -signal.analysis.importance,
                    -signal.analysis.confidence,
                    signal.published_at,
                    signal.item_id,
                ),
            )
        )
        if not ordered:
            return PolicyDecision(False, (), (), "no_new_content", RunStatus.SKIPPED)

        if edition is ReportEdition.MIDDAY:
            selected = tuple(
                signal
                for signal in ordered
                if signal.analysis.importance >= self.midday_min_importance
            )
            deferred = tuple(signal for signal in ordered if signal not in selected)
            if not selected:
                return PolicyDecision(False, (), deferred, "no_high_importance_signal", RunStatus.SKIPPED)
            return PolicyDecision(True, selected, deferred, "high_importance_signal", RunStatus.SUCCEEDED)

        if edition in {ReportEdition.MORNING, ReportEdition.EVENING}:
            high_value = tuple(
                signal for signal in ordered if signal.analysis.importance > self.low_priority_max
            )
            if not high_value:
                return PolicyDecision(False, (), ordered, "low_value_deferred_to_evening", RunStatus.SKIPPED)
            # Include low-priority context once at least one useful signal exists.
            return PolicyDecision(True, ordered, (), "new_content", RunStatus.SUCCEEDED)

        if edition is ReportEdition.WEEKLY:
            return PolicyDecision(True, ordered, (), "weekly_window_has_content", RunStatus.SUCCEEDED)

        # Ad-hoc reports preserve the exact caller-provided population after
        # deterministic ordering. They remain drafts until explicitly published.
        return PolicyDecision(True, ordered, (), "ad_hoc_requested", RunStatus.SUCCEEDED)
