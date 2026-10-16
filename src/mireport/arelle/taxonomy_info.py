"""Arelle plugin that extracts taxonomy (and optionally UTR) information.

This file is deliberately a thin harness: Arelle loads it by file path
(``plugins=__file__``) as its own module, so anything living here exists
twice when the plugin runs in-process. The actual extraction logic lives in
:mod:`mireport.arelle.taxonomy_extraction`, which both sides import by name.
The same trick lets the caller and plugin share a per-run context -- see
:mod:`mireport.arelle.taxonomy_info_run`.

Use :func:`callArelleForTaxonomyInfo` to run the plugin via the Arelle
Session API, or pass this file to ``arelleCmdLine --plugins``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from arelle.api.Session import Session
from arelle.Cntlr import Cntlr
from arelle.ModelXbrl import ModelXbrl
from arelle.RuntimeOptions import RuntimeOptions, RuntimeOptionValue
from arelle.utils.PluginData import PluginData

import mireport
from mireport.arelle.support import (
    ArelleProcessingResult,
    ArelleRelatedException,
)
from mireport.arelle.taxonomy_extraction import (
    TaxonomyInfoExtractor,
    UTRInfoExtractor,
    writeDataFile,
)
from mireport.arelle.taxonomy_info_run import TaxonomyInfoRunRegistry
from mireport.entrypoints import entryPointSetOf
from mireport.exceptions import TaxonomyException
from mireport.version import VersionInformationTuple

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


PLUGIN_NAME = "Taxonomy Information Extractor"
PLUGIN_VERSION = mireport.__version__
PLUGIN_INFO = VersionInformationTuple(PLUGIN_NAME, PLUGIN_VERSION)


def callArelleForTaxonomyInfo(
    entry_point: str | Iterable[str],
    taxonomy_zips: Sequence[Path],
    taxonomy_json_path: Path | str,
    utr_json_path: Path | str | None = None,
    *,
    writeDataDespiteErrors: bool = False,
) -> ArelleProcessingResult:
    try:
        entryPointSet = entryPointSetOf(entry_point)
    except TaxonomyException as e:
        raise ArelleRelatedException(f"Unusable entry point: {e}") from e
    runToken = TaxonomyInfoRunRegistry.open(entryPointSet=entryPointSet)
    # N.B. paths must cross the Arelle boundary as str: RuntimeOptions applies
    # pluginOptions with a bare setattr() so a Path would survive today, but
    # RuntimeOptionValue does not admit Path so that is not contractual.
    pluginOptions: dict[str, RuntimeOptionValue] = {
        "taxonomyDataFile": str(taxonomy_json_path),
        "runToken": runToken,
    }
    if writeDataDespiteErrors:
        pluginOptions["writeDataDespiteErrors"] = True
    utrValidation = False
    if utr_json_path is not None:
        pluginOptions["utrDataFile"] = str(utr_json_path)
        utrValidation = True

    # An entry point may name several documents that together form one DTS.
    # Importing the rest keeps them in the entry point's DTS; passing them all as
    # entry points would instead load each as its own DTS. The set has no
    # order, so which one is Arelle's entrypointFile is arbitrary: the first
    # in sorted order, for repeatability.
    entrypointFile, *importedEntryPointURLs = sorted(entryPointSet)

    options = RuntimeOptions(
        abortOnMajorError=True,
        entrypointFile=entrypointFile,
        importFiles="|".join(importedEntryPointURLs) or None,
        internetConnectivity="offline",
        formulaAction="none",
        keepOpen=False,
        logFile="logToBuffer",
        logFormat="%(message)s",
        logPropagate=False,
        packages=[str(z) for z in taxonomy_zips],
        pluginOptions=pluginOptions,
        plugins=__file__,
        validate=True,
        utrValidate=utrValidation,
    )
    try:
        with Session() as session:
            session.run(
                options,
                logFilters=[],
            )
            results = ArelleProcessingResult.fromSession(session)
    finally:
        run = TaxonomyInfoRunRegistry.close(runToken)
    results.addDiagnostics(run.diagnostics)
    return results


@dataclass
class TaxonomyInfoPluginData(PluginData):
    Taxonomy: dict = field(default_factory=dict)
    UTR: dict = field(default_factory=dict)


def pluginData(cntlr: Cntlr) -> TaxonomyInfoPluginData:
    pluginData = cntlr.getPluginData(PLUGIN_NAME)
    if pluginData is None:
        pluginData = TaxonomyInfoPluginData(PLUGIN_NAME)
        cntlr.setPluginData(pluginData)
    if not isinstance(pluginData, TaxonomyInfoPluginData):
        raise ArelleRelatedException(
            f"Plugin data for {PLUGIN_NAME!r} has unexpected type {type(pluginData)!r}"
        )
    return pluginData


def runTaxonomyInfo(
    cntlr: Cntlr,
    options: RuntimeOptions,
    modelXbrl: ModelXbrl,
    *args: Any,
    **kwargs: Any,
) -> None:
    start_ns = time.perf_counter_ns()
    cntlr.addToLog(f"{PLUGIN_INFO} starting.")
    pdata = pluginData(cntlr)
    extractor = TaxonomyInfoExtractor(cntlr, options, modelXbrl)
    taxonomy = extractor.extract()
    if extractor.diagnostics.hasErrors and not getattr(
        options, "writeDataDespiteErrors", False
    ):
        cntlr.addToLog(
            "Taxonomy data not written: extraction reported errors (see diagnostics)",
            level=logging.ERROR,
        )
        return
    pdata.Taxonomy.update(taxonomy)
    if options.utrValidate:
        cntlr.addToLog("UTR validation is on so attempting to process UTR entries")
        pdata.UTR.update(UTRInfoExtractor(cntlr, modelXbrl).extract())
    if (jsonPath := getattr(options, "taxonomyDataFile", None)) is not None:
        writeDataFile(cntlr, jsonPath, "Taxonomy", pdata.Taxonomy)
    if (jsonPath := getattr(options, "utrDataFile", None)) is not None:
        writeDataFile(cntlr, jsonPath, "UTR", pdata.UTR)
    elapsed_s = (time.perf_counter_ns() - start_ns) / 1_000_000_000
    cntlr.addToLog(f"{PLUGIN_NAME} completed ({elapsed_s:,.2f} seconds elapsed).")


__pluginInfo__ = {
    "name": PLUGIN_NAME,
    "description": "Extracts information from a taxonomy",
    "license": "MIT",
    "version": PLUGIN_VERSION,
    "author": "Stuart Rowan",
    "copyright": " Copyright :: EFRAG :: 2026",
    "CntlrCmdLine.Xbrl.Run": runTaxonomyInfo,
}
