"""Arelle plugin that extracts taxonomy (and optionally UTR) information.

This file is deliberately a thin harness: Arelle loads it by file path
(``plugins=__file__``) as its own module, so anything living here exists
twice when the plugin runs in-process. The actual extraction logic lives in
:mod:`mireport.arelle.taxonomy_extraction`, which both sides import by name.
The same trick makes :class:`DiagnosticCollector` work as a hand-back
channel — see its docstring in :mod:`mireport.arelle.diagnostics`.

Use :func:`callArelleForTaxonomyInfo` to run the plugin via the Arelle
Session API, or pass this file to ``arelleCmdLine --plugins``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

from arelle.api.Session import Session
from arelle.Cntlr import Cntlr
from arelle.ModelXbrl import ModelXbrl
from arelle.RuntimeOptions import RuntimeOptions, RuntimeOptionValue
from arelle.utils.PluginData import PluginData

from mireport.arelle.diagnostics import DiagnosticCollector
from mireport.arelle.support import (
    ArelleProcessingResult,
    ArelleRelatedException,
)
from mireport.arelle.taxonomy_extraction import (
    TaxonomyInfoExtractor,
    UTRInfoExtractor,
    writeDataFile,
)

PLUGIN_NAME = "Taxonomy Information Extractor"


def callArelleForTaxonomyInfo(
    entry_point: str,
    taxonomy_zips: list[str],
    taxonomy_json_path: Path | str,
    utr_json_path: Path | str | None = None,
) -> ArelleProcessingResult:
    diagnosticsToken = DiagnosticCollector.open()
    # N.B. paths must cross the Arelle boundary as str: RuntimeOptions applies
    # pluginOptions with a bare setattr() so a Path would survive today, but
    # RuntimeOptionValue does not admit Path so that is not contractual.
    pluginOptions: dict[str, RuntimeOptionValue] = {
        "taxonomyDataFile": str(taxonomy_json_path),
        "diagnosticsToken": diagnosticsToken,
    }
    utrValidation = False
    if utr_json_path is not None:
        pluginOptions["utrDataFile"] = str(utr_json_path)
        utrValidation = True

    options = RuntimeOptions(
        abortOnMajorError=True,
        entrypointFile=entry_point,
        internetConnectivity="offline",
        formulaAction="none",
        keepOpen=False,
        logFile="logToBuffer",
        logFormat="%(message)s",
        logPropagate=False,
        packages=taxonomy_zips,
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
        diagnostics = DiagnosticCollector.close(diagnosticsToken)
    results.addDiagnostics(diagnostics)
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
    cntlr.addToLog(f"{PLUGIN_NAME} starting.")
    pdata = pluginData(cntlr)
    extractor = TaxonomyInfoExtractor(cntlr, options, modelXbrl)
    pdata.Taxonomy.update(extractor.extract())
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
    "license": "Apache-2.0",
    "version": "0.7",
    "author": "Stuart Rowan",
    "copyright": " Copyright :; EFRAG :: 2025",
    "CntlrCmdLine.Xbrl.Run": runTaxonomyInfo,
}
