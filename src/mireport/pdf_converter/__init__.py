"""Conversion of supplementary PDFs into Inline XBRL document set members.

Output is a well-formed, self-contained XHTML document carrying an empty, hidden
``ix:header`` and no XBRL data of its own, ready to be passed as a
``docsetMember`` to :func:`mireport.report.reportpackage.buildReportPackage`.

Conversion uses a system binary, not a Python dependency. See
:mod:`mireport.pdf_converter._backends`.
"""

from __future__ import annotations

from mireport.pdf_converter._backends import (
    BACKENDS_IN_PREFERENCE_ORDER,
    PdfConverterBackend,
    ResolvedConverter,
)
from mireport.pdf_converter._batch import (
    PdfBatchResult,
    PdfSupplementaryMode,
    convertSupplementaryPdfs,
)
from mireport.pdf_converter._convert import convertPdfToHtml, outputFilenameFor
from mireport.pdf_converter._exceptions import (
    PdfConversionError,
    PdfToolNotFoundError,
)
from mireport.pdf_converter._merge import (
    MergedAnnex,
    buildMergedAnnex,
    mergeAnnexesIntoReport,
)
from mireport.pdf_converter._run import (
    DEFAULT_TIMEOUT_SECONDS,
    PATH_ENV_VAR,
    availableConverters,
    findConverter,
)

__all__ = [
    "BACKENDS_IN_PREFERENCE_ORDER",
    "DEFAULT_TIMEOUT_SECONDS",
    "PATH_ENV_VAR",
    "MergedAnnex",
    "PdfBatchResult",
    "PdfConversionError",
    "PdfConverterBackend",
    "PdfSupplementaryMode",
    "PdfToolNotFoundError",
    "ResolvedConverter",
    "availableConverters",
    "buildMergedAnnex",
    "convertPdfToHtml",
    "convertSupplementaryPdfs",
    "findConverter",
    "mergeAnnexesIntoReport",
    "outputFilenameFor",
]
