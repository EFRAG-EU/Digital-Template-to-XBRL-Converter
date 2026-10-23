"""The steps run on a converted report once its results page can be shown:
Arelle validates the report package, then the xBRL-JSON written from it.

Each step's :class:`~mireport.conversionresults.StepResults` is kept in a
:class:`StepStore`, a cache every process serving the app shares, rather than
in the session, which is rewritten whole by whichever request saves it last.
One process at a time runs a conversion's steps, the one holding its claim;
any request that finds them unfinished and unclaimed (the process running them
was restarted, say) runs them again, skipping those already finished.

They run on a background thread, one per process: the results page is shown
straight away and polls for the steps' progress. ``RUN_STEPS_INLINE`` runs them
within the request instead, so the page is complete when it is first shown.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TYPE_CHECKING

from cachelib.simple import SimpleCache
from flask import current_app

from mireport.arelle.report_info import ARELLE_VERSION_INFORMATION
from mireport.conversionresults import ReportResults, Step, StepResults

from .app_setup import getArelle

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

    from cachelib.base import BaseCache
    from flask import Flask

    from mireport.arelle.report_info import ArelleReportProcessor
    from mireport.filesupport import FilelikeAndFileName

    from .conversion import Conversion

L = logging.getLogger(__name__)

# How long a claim lasts without being renewed. Longer than any one step takes:
# a claim that lapses lets another process start the steps again.
CLAIM_TIMEOUT_SECONDS = 600

_EXTENSION_KEY = "stepStore"

# One worker: Arelle runs one session at a time per process anyway
# (BIG_ARELLE_LOCK). Made on first use, and again after a fork, which leaves a
# child no threads.
_executor: ThreadPoolExecutor | None = None
_executorPid: int | None = None
_executorLock = threading.Lock()


class StepStore:
    """Each conversion's step results, and who is running its steps."""

    def __init__(self, cache: BaseCache, timeoutSeconds: int) -> None:
        self._cache = cache
        self._timeout = timeoutSeconds

    @classmethod
    def fromFlask(cls) -> StepStore:
        return current_app.extensions[_EXTENSION_KEY]

    def install(self, app: Flask) -> None:
        app.extensions[_EXTENSION_KEY] = self

    def load(self, conversionId: str) -> dict[Step, StepResults]:
        stored = self._cache.get_many(*(self._key(conversionId, s) for s in Step))
        return {
            step: StepResults.fromDict(raw)
            for step, raw in zip(Step, stored, strict=True)
            if raw is not None
        }

    def save(self, conversionId: str, results: StepResults) -> None:
        self._cache.set(
            self._key(conversionId, results.step), results.toDict(), self._timeout
        )

    def forget(self, conversionId: str) -> None:
        self._cache.delete_many(*(self._key(conversionId, s) for s in Step))

    def claim(self, conversionId: str) -> bool:
        """Claim the running of these steps; False if someone already has."""
        return bool(
            self._cache.add(
                self._key(conversionId, "claim"), os.getpid(), CLAIM_TIMEOUT_SECONDS
            )
        )

    def renewClaim(self, conversionId: str) -> None:
        self._cache.set(
            self._key(conversionId, "claim"), os.getpid(), CLAIM_TIMEOUT_SECONDS
        )

    def release(self, conversionId: str) -> None:
        self._cache.delete(self._key(conversionId, "claim"))

    @staticmethod
    def _key(conversionId: str, part: str) -> str:
        return f"{conversionId}:{part}"


def configureStepStore(app: Flask) -> None:
    """Keep step results in ``STEP_CACHELIB`` if that is configured, else in
    the redis the sessions use, else in memory: developer mode serves from one
    process. They last as long as the sessions do."""
    if (cache := app.config.get("STEP_CACHELIB")) is None:
        if (redis := app.config.get("SESSION_REDIS")) is not None:
            from cachelib.redis import RedisCache

            cache = RedisCache(host=redis, key_prefix="converter-steps:")
        else:
            cache = SimpleCache()
    lifetime = app.config["PERMANENT_SESSION_LIFETIME"]
    StepStore(cache, int(lifetime.total_seconds())).install(app)


def runSteps(
    store: StepStore,
    conversionId: str,
    reportPackage: FilelikeAndFileName,
    xbrlJson: FilelikeAndFileName,
    arelle: ArelleReportProcessor,
) -> None:
    """Run the steps not yet finished, saving each one's progress as it goes.
    Needs the claim, which it releases; touches no session, so it can run
    outside a request."""
    arelleDetail = (
        f"Using Arelle (XBRL Certified Software™) [{ARELLE_VERSION_INFORMATION}]"
    )
    work: dict[Step, Callable[[], StepResults]] = {
        Step.PACKAGE: lambda: arelle.validateReportPackage(
            reportPackage
        ).validationResults(Step.PACKAGE, detail=arelleDetail),
        Step.XBRL_JSON: lambda: arelle.validateXbrlJson(xbrlJson).validationResults(
            Step.XBRL_JSON, detail=f"{xbrlJson.filename}. {arelleDetail}"
        ),
        Step.VIEWER: lambda: StepResults.skipped(
            Step.VIEWER, "The viewer is generated when first asked for."
        ),
    }
    try:
        finished = {s for s, r in store.load(conversionId).items() if r.isFinished}
        for step, run in work.items():
            if step in finished:
                continue
            store.renewClaim(conversionId)
            resultsClass = step.resultsClass
            store.save(conversionId, resultsClass.running(step))
            try:
                store.save(conversionId, run())
            except Exception as e:
                L.exception(f"{step.label} failed for conversion {conversionId}")
                store.save(conversionId, resultsClass.failed(step, e))
    finally:
        store.release(conversionId)


def _runInBackground(job: Callable[[], None]) -> None:
    global _executor, _executorPid
    with _executorLock:
        if _executor is None or _executorPid != os.getpid():
            _executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="conversion-steps"
            )
            _executorPid = os.getpid()
        future = _executor.submit(job)
    future.add_done_callback(_logFailure)


def _logFailure(future: Future[None]) -> None:
    if (e := future.exception()) is not None:
        L.error("Conversion steps stopped unexpectedly", exc_info=e)


def reportResults(conv: Conversion) -> ReportResults | None:
    """The conversion's results with its steps', starting any steps that are
    unfinished and that no one is running."""
    if (conversion := conv.results) is None:
        return None
    store = StepStore.fromFlask()
    results = ReportResults(conversion, store.load(conv.id))
    if not results.isComplete and store.claim(conv.id):
        reportPackage, xbrlJson = conv.zip, conv.json
        assert reportPackage is not None and xbrlJson is not None, (
            "A successful conversion has a report package and its xBRL-JSON"
        )
        job = partial(runSteps, store, conv.id, reportPackage, xbrlJson, getArelle())
        if current_app.config.get("RUN_STEPS_INLINE", False):
            job()
            results = ReportResults(conversion, store.load(conv.id))
        else:
            _runInBackground(job)
    return results
