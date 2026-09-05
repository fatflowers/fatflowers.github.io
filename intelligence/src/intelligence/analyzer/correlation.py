"""Deterministic preparation of cross-event windows for the analysis agent."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict, Iterable

from .schema import AnalysisResult


@dataclass(frozen=True)
class CorrelationEvent:
    item_id: str
    target: str
    target_tags: tuple[str, ...]
    analysis: AnalysisResult


@dataclass(frozen=True)
class CorrelationCandidate:
    dimension: str
    key: str
    item_ids: tuple[str, ...]
    evidence_urls: tuple[str, ...]
    max_importance: int
    mean_confidence: float


def correlate_events(events: Iterable[CorrelationEvent]) -> tuple[CorrelationCandidate, ...]:
    """Build stable multi-event groups that are eligible for trend analysis.

    This function does not invent a trend. It enforces the two-event evidence
    floor and prepares bounded, traceable candidates for the correlation prompt.
    """

    groups: DefaultDict[tuple[str, str], list[CorrelationEvent]] = defaultdict(list)
    for event in events:
        groups[("target", event.target)].append(event)
        for tag in event.target_tags:
            groups[("tag", tag)].append(event)
        for topic in event.analysis.topics:
            groups[("topic", topic)].append(event)

    candidates: list[CorrelationCandidate] = []
    for (dimension, key), grouped in groups.items():
        unique = {event.item_id: event for event in grouped}
        if len(unique) < 2:
            continue
        ordered = tuple(unique[item_id] for item_id in sorted(unique))
        evidence_urls = tuple(
            sorted({evidence.url for event in ordered for evidence in event.analysis.evidence})
        )
        candidates.append(
            CorrelationCandidate(
                dimension=dimension,
                key=key,
                item_ids=tuple(event.item_id for event in ordered),
                evidence_urls=evidence_urls,
                max_importance=max(event.analysis.importance for event in ordered),
                mean_confidence=round(
                    sum(event.analysis.confidence for event in ordered) / len(ordered), 4
                ),
            )
        )
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                -item.max_importance,
                -item.mean_confidence,
                item.dimension,
                item.key.casefold(),
                item.key,
            ),
        )
    )
