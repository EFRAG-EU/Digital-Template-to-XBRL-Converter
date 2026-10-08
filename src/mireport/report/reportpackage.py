"""Assembly of XBRL report packages (the ``.zip`` an Inline XBRL report ships in).

Kept separate from :mod:`mireport.report.inlinereport` so that attachments are an
argument rather than mutable state on ``InlineReport``: everything the zip needs
arrives at the one call that writes it.

The directory layout is load-bearing. Arelle decides what to load out of a report
package from the structure alone (``arelle.packages.report.ReportPackage``):

- a file directly under ``{top}/reports/`` is a report entry point in its own
  right, so a second one there would be validated as a second XBRL report;
- files sharing a *sub*directory of ``reports/`` collapse into a single
  multi-file entry — an Inline XBRL document set;
- subdirectory entries are discarded entirely whenever a top-level report also
  exists.

Hence: as soon as there are document set members, the tagged report moves down
into the document set directory with them, and nothing is left directly in
``reports/``. Content that is not part of the document set (the original PDFs)
goes to ``attachments/``, outside ``reports/`` altogether, where no discovery
path looks at it.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from mireport.filesupport import FilelikeAndFileName, zipSafeString

if TYPE_CHECKING:
    from collections.abc import Sequence

UNCONSTRAINED_REPORT_PACKAGE_JSON = b"""{
    "documentInfo": {
        "documentType": "https://xbrl.org/report-package/2023"
    }
}"""

INLINE_REPORT_PACKAGE_JSON = b"""{
    "documentInfo": {
        "documentType": "https://xbrl.org/report-package/2023/xbri"
    }
}"""

METADATA_DIRECTORY = "META-INF"
REPORTS_DIRECTORY = "reports"
ATTACHMENTS_DIRECTORY = "attachments"


def _safeFilename(filename: str, *, fallback: str) -> str:
    """Reduce an arbitrary filename to a single zip-safe path segment.

    Directory parts are discarded (on either separator — Arelle rejects a
    package outright if any entry uses a backslash) before the remainder is run
    through the same sanitiser used for entity names.
    """
    lastSegment = filename.replace("\\", "/").rpartition("/")[2]
    return zipSafeString(lastSegment, fallback=fallback)


def _uniqueFilename(filename: str, taken: set[str]) -> str:
    """Return @filename, or the first ``{stem}_{n}{suffix}`` variant not in @taken.

    Colliding names are suffixed rather than rejected: attachments are additive
    and optional, so a name clash must never sink an otherwise-valid conversion.
    @taken is updated with whatever is returned.
    """
    candidate = filename
    if candidate in taken:
        path = PurePosixPath(filename)
        stem, suffix = path.stem, path.suffix
        n = 1
        while (candidate := f"{stem}_{n}{suffix}") in taken:
            n += 1
    taken.add(candidate)
    return candidate


def buildReportPackage(
    report: FilelikeAndFileName,
    *,
    topLevel: str,
    docsetMembers: Sequence[FilelikeAndFileName] = (),
    attachments: Sequence[FilelikeAndFileName] = (),
) -> FilelikeAndFileName:
    """Build an unconstrained report package containing @report.

    :param topLevel: name of the package's single top-level directory.
    :param docsetMembers: further Inline XBRL documents to be loaded as one
        document set with @report. When non-empty the report moves into a
        subdirectory of ``reports/`` alongside them. Written in the order given,
        after the report, because a document set's order is its zip order.
    :param attachments: content to travel with the report without being part of
        it (the source PDFs). Written to ``attachments/``.
    """
    top = zipSafeString(topLevel, fallback="report")
    content = BytesIO()
    with zipfile.ZipFile(
        content, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as zf:
        zf.writestr(
            zinfo_or_arcname=f"{top}/{METADATA_DIRECTORY}/reportPackage.json",
            data=UNCONSTRAINED_REPORT_PACKAGE_JSON,
        )

        reportFilename = _safeFilename(report.filename, fallback="report.html")
        if docsetMembers:
            reportDir = (
                f"{top}/{REPORTS_DIRECTORY}/{PurePosixPath(reportFilename).stem}"
            )
        else:
            reportDir = f"{top}/{REPORTS_DIRECTORY}"

        # Names are tracked per directory: a member and an attachment sharing a
        # name are in different directories and so do not collide.
        usedInReportDir: set[str] = set()
        for file in (report, *docsetMembers):
            name = _uniqueFilename(
                _safeFilename(file.filename, fallback="report.html"), usedInReportDir
            )
            zf.writestr(zinfo_or_arcname=f"{reportDir}/{name}", data=file.fileContent)

        usedInAttachmentDir: set[str] = set()
        for attachment in attachments:
            name = _uniqueFilename(
                _safeFilename(attachment.filename, fallback="attachment"),
                usedInAttachmentDir,
            )
            zf.writestr(
                zinfo_or_arcname=f"{top}/{ATTACHMENTS_DIRECTORY}/{name}",
                data=attachment.fileContent,
            )

    return FilelikeAndFileName(
        fileContent=content.getvalue(), filename=f"{top}_XBRL_Report.zip"
    )
