"""Rollup aggregation: the jobs that build `agg_*` tables, and the runner.

Three modules, one responsibility each:

* :mod:`~app.services.analytics.aggregation.base` — the job protocol
  (``run(db, bucket_date, tz_generation) -> JobRunResult``) and the ``JOBS``
  registry every other surface resolves job names through.
* :mod:`~app.services.analytics.aggregation.jobs` — the first three jobs, and
  the two write patterns (upsert-overwrite for fixed-key buckets,
  delete-and-reinsert for dimensioned ones) that make each one idempotent:
  ``order_daily``, ``order_hourly``, ``product_daily``. Its module docstring is
  the reference for both patterns and its helpers are imported by the job
  modules below rather than copied.
* :mod:`~app.services.analytics.aggregation.jobs_customer` — ``customer_daily``
  and ``customer_snapshot``.
* :mod:`~app.services.analytics.aggregation.jobs_ops` — ``funnel_daily``,
  ``inventory_daily`` and ``shipment_daily``.

* :mod:`~app.services.analytics.aggregation.jobs_finance` — ``payment_daily``,
  ``shipment_geo_daily``, ``promo_daily`` and ``cohort_monthly``.

**All twelve rollups are implemented and registered.** None was ever stubbed:
a job registered under a real name but not actually built would report success,
advance a watermark, and make an unbuilt table indistinguishable from a quiet
one. Absent is honest; a stub is not.

* :mod:`~app.services.analytics.aggregation.runner` — scheduling, the time
  budget, per-bucket transactions, the ``analytics_sync_runs`` log, and the
  drain of the dirty-bucket recompute queue.

Importing anything from this package registers the jobs. That import lives here,
before the runner, so no import path can reach ``AggregationRunner`` with an
empty registry and fail with "unknown aggregation job" on a job that plainly
exists.
"""
from __future__ import annotations

from app.services.analytics.aggregation.base import (
    JOBS,
    AggregationJob,
    JobRunResult,
    get_job,
    register,
)
from app.services.analytics.aggregation import jobs as jobs  # noqa: F401 - registers

# Imported for their side effect: each module calls `register()` at import time,
# and `jobs` must come first because these two import its write-pattern helpers.
from app.services.analytics.aggregation import (  # noqa: F401 - registers
    jobs_customer as jobs_customer,
)
from app.services.analytics.aggregation import (  # noqa: F401 - registers
    jobs_ops as jobs_ops,
)
from app.services.analytics.aggregation import (  # noqa: F401 - registers
    jobs_finance as jobs_finance,
)
from app.services.analytics.aggregation import (  # noqa: F401 - registers
    jobs_ga4 as jobs_ga4,
)
from app.services.analytics.aggregation import (  # noqa: F401 - registers
    jobs_basket as jobs_basket,
)
from app.services.analytics.aggregation import (  # noqa: F401 - registers
    jobs_cx as jobs_cx,
)
from app.services.analytics.aggregation import (  # noqa: F401 - registers
    jobs_loyalty as jobs_loyalty,
)
from app.services.analytics.aggregation import (  # noqa: F401 - registers
    jobs_shadow as jobs_shadow,
)
from app.services.analytics.aggregation import (  # noqa: F401 - registers
    jobs_settlement as jobs_settlement,
)
from app.services.analytics.aggregation.runner import (
    DEFAULT_BUDGET_MS,
    AggregationRunner,
)

__all__ = [
    "JOBS",
    "AggregationJob",
    "JobRunResult",
    "AggregationRunner",
    "DEFAULT_BUDGET_MS",
    "get_job",
    "register",
]
