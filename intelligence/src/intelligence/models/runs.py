"""Pipeline run query models."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PipelineRun:
    id: str
    run_type: str
    trigger_type: str
    run_status: RunStatus
    created_at: str
    multica_run_id: Optional[str] = None
    target_id: Optional[str] = None
    channel_id: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    attempt: int = 1
    item_count: int = 0
    error_code: Optional[str] = None
    error_summary: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "PipelineRun":
        known = {
            "id",
            "run_type",
            "trigger_type",
            "run_status",
            "created_at",
            "multica_run_id",
            "target_id",
            "channel_id",
            "started_at",
            "finished_at",
            "attempt",
            "item_count",
            "error_code",
            "error_summary",
            "metadata",
        }
        metadata = dict(value.get("metadata") or {})
        metadata.update({key: val for key, val in value.items() if key not in known})
        return cls(
            id=str(value["id"]),
            run_type=str(value["run_type"]),
            trigger_type=str(value["trigger_type"]),
            run_status=RunStatus(str(value["run_status"])),
            created_at=str(value["created_at"]),
            multica_run_id=value.get("multica_run_id"),
            target_id=value.get("target_id"),
            channel_id=value.get("channel_id"),
            started_at=value.get("started_at"),
            finished_at=value.get("finished_at"),
            attempt=int(value.get("attempt", 1)),
            item_count=int(value.get("item_count", 0)),
            error_code=value.get("error_code"),
            error_summary=value.get("error_summary"),
            metadata=metadata,
        )

    def to_dict(self) -> Dict[str, Any]:
        result = dict(self.__dict__)
        result["run_status"] = self.run_status.value
        return result
