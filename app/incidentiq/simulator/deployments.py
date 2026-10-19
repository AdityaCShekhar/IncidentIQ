"""Bounded, run-scoped simulator controls; never registered as diagnostic tools."""

from collections.abc import Callable
from threading import Lock
from time import monotonic, time_ns
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

Revision = Literal["checkout-v1", "checkout-v2"]


class DeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    revision: Revision


class DeploymentEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    previous_revision: Revision
    revision: Revision
    timestamp_unix_nano: int


class DeploymentResult(BaseModel):
    run_id: UUID
    revision: Revision
    changed: bool
    event: DeploymentEvent | None


class DeploymentCapacityError(Exception):
    pass


class DeploymentRegistry:
    """Only v2 overrides occupy slots. Repeated activation does not renew the TTL."""

    def __init__(
        self,
        *,
        max_active: int = 64,
        ttl_seconds: float = 600,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not 1 <= max_active <= 64 or not 0 < ttl_seconds <= 600:
            raise ValueError("Invalid deployment registry bounds")
        self._active: dict[UUID, tuple[float, DeploymentEvent]] = {}
        self._max_active = max_active
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = Lock()

    def _expire(self) -> None:
        now = self._clock()
        for run_id, (deadline, _) in list(self._active.items()):
            if deadline <= now:
                del self._active[run_id]

    def revision(self, run_id: UUID) -> Revision:
        with self._lock:
            self._expire()
            return "checkout-v2" if run_id in self._active else "checkout-v1"

    def activate(self, request: DeploymentRequest) -> DeploymentResult:
        with self._lock:
            self._expire()
            existing = self._active.get(request.run_id)
            previous: Revision = "checkout-v2" if existing else "checkout-v1"
            if previous == request.revision:
                return DeploymentResult(
                    run_id=request.run_id,
                    revision=previous,
                    changed=False,
                    event=existing[1] if existing else None,
                )
            if request.revision == "checkout-v2" and len(self._active) >= self._max_active:
                raise DeploymentCapacityError
            event = DeploymentEvent(
                id=uuid4(),
                run_id=request.run_id,
                previous_revision=previous,
                revision=request.revision,
                timestamp_unix_nano=time_ns(),
            )
            if request.revision == "checkout-v2":
                self._active[request.run_id] = (self._clock() + self._ttl, event)
            else:
                self._active.pop(request.run_id, None)
            return DeploymentResult(
                run_id=request.run_id, revision=request.revision, changed=True, event=event
            )
