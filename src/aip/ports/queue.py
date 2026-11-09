"""Job queue port and an in-memory adapter with dead-letter semantics."""

import uuid
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Job:
    id: str
    body: dict[str, Any]
    receipt: str
    receive_count: int = 1


class JobQueue(Protocol):
    def send(self, body: dict[str, Any]) -> str: ...

    def receive(self, max_messages: int = 1, wait_s: int = 0) -> list[Job]: ...

    def delete(self, job: Job) -> None: ...

    def dlq_depth(self) -> int: ...

    def redrive(self, max_messages: int = 10) -> int: ...


class InMemoryQueue:
    """A job received more than ``max_receive_count`` times without a delete moves to the DLQ.

    Every receive makes the job visible again at once (visibility timeout 0), so a worker that
    does not delete sees the job again on the next receive.
    """

    def __init__(self, max_receive_count: int = 3) -> None:
        self.max_receive_count = max_receive_count
        self._main: dict[str, Job] = {}
        self._dlq: dict[str, Job] = {}

    def send(self, body: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        self._main[job_id] = Job(id=job_id, body=dict(body), receipt="", receive_count=0)
        return job_id

    def receive(self, max_messages: int = 1, wait_s: int = 0) -> list[Job]:
        out: list[Job] = []
        for job_id in list(self._main):
            if len(out) >= max_messages:
                break
            job = self._main[job_id]
            if job.receive_count >= self.max_receive_count:
                self._dlq[job_id] = self._main.pop(job_id)
                continue
            job.receive_count += 1
            job.receipt = uuid.uuid4().hex
            out.append(Job(job.id, dict(job.body), job.receipt, job.receive_count))
        return out

    def delete(self, job: Job) -> None:
        self._main.pop(job.id, None)

    def dlq_depth(self) -> int:
        for job_id in list(self._main):
            if self._main[job_id].receive_count >= self.max_receive_count:
                self._dlq[job_id] = self._main.pop(job_id)
        return len(self._dlq)

    def redrive(self, max_messages: int = 10) -> int:
        self.dlq_depth()
        moved = 0
        for job_id in list(self._dlq)[:max_messages]:
            job = self._dlq.pop(job_id)
            job.receive_count = 0
            self._main[job_id] = job
            moved += 1
        return moved
