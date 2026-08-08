"""The aggregation job protocol and its registry.

A job is deliberately tiny: a name, and a ``run(db, bucket_date, tz_generation)``
that rebuilds **exactly one bucket** and returns what it did. Everything else —
scheduling, budgets, transactions, run logging, the queue — belongs to
:mod:`app.services.analytics.aggregation.runner`, so a job never needs to know
whether it was triggered by cron, by an admin button, or by a dirty-bucket
queue row.

Why one bucket at a time
------------------------
The unit of recomputation is the unit of correctness. ``analytics_recompute_queue``
enqueues ``(job, bucket_date, tz_generation)`` and the runner processes one such
tuple per transaction, so a job that internally spanned several days would make
"this bucket is now rebuilt" unprovable — a crash halfway through would leave
some days rebuilt under the new inputs and some not, with nothing recording
which. One bucket per call also makes every job trivially resumable and makes
the budget check in the runner meaningful.

Why the result is a value, not a log line
-----------------------------------------
``rows_written`` / ``rows_deleted`` are written to ``analytics_sync_runs``, where
their *ratio* is a health signal: deletes far exceeding writes means a bucket
lost its source rows. A job that only logged its counts could not feed that.

``warnings`` carries anything the job noticed but could not fix — a revenue
bridge that failed to balance, a dimension it had to write as the ``'-'``
sentinel. They ride back to the caller rather than being swallowed, because a
number that is quietly wrong is the failure mode this whole subsystem exists to
prevent.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

__all__ = [
    "JobRunResult",
    "AggregationJob",
    "JOBS",
    "register",
    "get_job",
]


@dataclass(frozen=True)
class JobRunResult:
    """What one ``(job, bucket)`` execution did.

    ``rows_written`` counts rollup rows the job *presented* to the database, not
    MySQL's ``rowcount``: an ``INSERT ... ON DUPLICATE KEY UPDATE`` reports 1 for
    an insert, 2 for an update and 0 when the update changed nothing, so
    ``rowcount`` cannot be summed into a meaningful total. The logical count is
    the one that answers "did this bucket get rebuilt".
    """

    rows_written: int = 0
    rows_deleted: int = 0
    warnings: tuple[str, ...] = ()

    def merged(self, other: "JobRunResult") -> "JobRunResult":
        """Accumulate another result into this one. Used by the runner only."""
        return replace(
            self,
            rows_written=self.rows_written + other.rows_written,
            rows_deleted=self.rows_deleted + other.rows_deleted,
            warnings=self.warnings + other.warnings,
        )


@runtime_checkable
class AggregationJob(Protocol):
    """Rebuilds one rollup bucket from the transactional tables.

    Contract every implementation must honour:

    * **Idempotent.** Running the same ``(bucket_date, tz_generation)`` twice
      leaves the table identical, never doubled. This is not an optimisation —
      the recompute queue's lease can expire and hand the same bucket to a second
      worker, and the runner retries. See the module docstring of
      :mod:`~app.services.analytics.aggregation.jobs` for the two write patterns
      that deliver it.
    * **Bucket-scoped.** It writes rows for ``bucket_date`` under
      ``tz_generation`` and touches nothing else. A job that also fixed up
      yesterday would make the queue's per-bucket accounting a lie.
    * **Store-local.** Day boundaries come from
      :mod:`app.services.analytics.timebox` and are never computed from UTC
      dates.
    * **No commit.** The runner owns the transaction, because "one bucket, one
      transaction" is what makes a budget-exhausted run leave a consistent —
      if incomplete — table.
    """

    name: str

    def run(
        self, db: Session, bucket_date: date, tz_generation: int
    ) -> JobRunResult: ...


#: Name -> job. The runner, the admin API and the queue drainer all resolve jobs
#: through this one mapping, so a queue row naming a job that no longer exists
#: fails loudly at lookup instead of being silently skipped.
JOBS: dict[str, AggregationJob] = {}


def register(job: AggregationJob) -> AggregationJob:
    """Add a job to :data:`JOBS`. Returns it, so it can be used as a decorator.

    Re-registering a name is refused rather than overwritten: two jobs writing
    the same rollup under one name would make the recompute queue's
    ``(job, bucket_date)`` key ambiguous, and whichever imported last would win
    non-deterministically.
    """
    if job.name in JOBS:
        raise ValueError(
            f"aggregation job {job.name!r} is already registered; job names are "
            "the key the recompute queue and the sync-run log both use"
        )
    JOBS[job.name] = job
    return job


def get_job(name: str) -> AggregationJob:
    """Resolve a job by name, with an error that names the alternatives."""
    try:
        return JOBS[name]
    except KeyError:
        raise KeyError(
            f"unknown aggregation job {name!r}; registered jobs are "
            f"{sorted(JOBS)}"
        ) from None
