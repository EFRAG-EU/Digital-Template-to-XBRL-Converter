from __future__ import annotations

import logging
import threading
import zipfile
from importlib.metadata import PackageNotFoundError, metadata, version
from io import BytesIO
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import BinaryIO

from arelle import PackageManager, PluginManager
from arelle.api.Session import Session
from arelle.CntlrCmdLine import RuntimeOptions

from mireport.arelle.support import (
    ArelleProcessingResult,
    ArelleRelatedException,
    ArelleVersionHolder,
)
from mireport.filesupport import FilelikeAndFileName
from mireport.report.inlinereport import UNCONSTRAINED_REPORT_PACKAGE_JSON
from mireport.version import VersionInformationTuple

BIG_ARELLE_LOCK = threading.Lock()

L = logging.getLogger(__name__)


def _singleFileFromResponseZip(stream: BytesIO, what: str) -> bytes:
    """Return the content of the single file Arelle wrote to its response
    zip, raising if the zip does not contain exactly one file."""
    stream.seek(0)
    with zipfile.ZipFile(stream, "r") as zf:
        entries = zf.infolist()
        if len(entries) != 1:
            raise ArelleRelatedException(
                f"{what} has gone wrong. Zip contents: {zf.namelist()}"
            )
        return zf.read(entries[0])


class ArelleReportProcessor:
    """Wrapper around the Arelle Session() API for the various validations and plugins wanted."""

    def __init__(
        self,
        *,
        taxonomyPackages: list[Path] | None = None,
        workOffline: bool = True,
    ):
        self.workOffline = bool(workOffline)
        self.taxonomyPackages: list[Path] = []
        if taxonomyPackages is not None:
            self.taxonomyPackages.extend(taxonomyPackages)

    def _run(
        self,
        reportPackage: FilelikeAndFileName,
        options: RuntimeOptions,
        responseZipStream: BinaryIO | None = None,
    ) -> ArelleProcessingResult:
        ###############################
        #  Arelle is _NOT_ thread safe.
        ###############################
        #
        # If you get rid of this lock then everyone will get each other's
        # results and files, as well as or instead of their own.
        #
        # Example AssertionError: xBRL JSON has gone wrong.['foo.json',
        # 'xbrlviewer.html', 'ixbrlviewer.js']
        #
        # One person's xBRL-JSON has ended up in the same output zip as an XBRL
        # viewer.
        #
        # Example AttributeError [Exception] Failed to complete request:
        # 'RuntimeOptions' object has no attribute 'useStubViewer' [' File
        # "C:\\Users\\stuar\\Documents\\efrag\\vsme-converter\\.venv\\Lib\\site-packages\\arelle\\CntlrCmdLine.py",
        # line 1250, in run\n pluginXbrlMethod(self, options, modelXbrl,
        # _entrypoint, sourceZipStream=sourceZipStream,
        # responseZipStream=responseZipStream)\n', ' File
        # "C:\\Users\\stuar\\Documents\\efrag\\vsme-converter\\.venv\\Lib\\site-packages\\iXBRLViewerPlugin\\__init__.py",
        # line 299, in commandLineRun\n iXBRLViewerCommandLineXbrlRun(cntlr,
        # options, modelXbrl, *args, **kwargs)\n', ' File
        # "C:\\Users\\stuar\\Documents\\efrag\\vsme-converter\\.venv\\Lib\\site-packages\\iXBRLViewerPlugin\\__init__.py",
        # line 226, in iXBRLViewerCommandLineXbrlRun\n pd.builder =
        # IXBRLViewerBuilder(cntlr, useStubViewer = options.useStubViewer,
        # features=getFeaturesFromOptions(options))\n ^^^^^^^^^^^^^^^^^^^^^\n']
        #
        #
        # So we use the BIG_ARELLE_LOCK to make sure we only call in to Arelle
        # one thread at time, thus making it safe.
        #
        with BIG_ARELLE_LOCK:
            try:
                try:
                    # These survive between calls to Session() so you end up
                    # with plugins activated when you didn't specify them, like
                    # the viewer plugin appearing in validateReportPackage()
                    # output. So hard reset them while protected by the
                    # BIG_ARELLE_LOCK. close() seems to do stuff that reset()
                    # forgot about.
                    PackageManager.reset()
                    PackageManager.close()
                    PluginManager.reset()
                    PluginManager.close()
                except Exception:
                    L.exception("Failed to reset Arelle package and plugin managers")
                with (
                    Session() as session,
                    reportPackage.fileLike() as requestZipStream,
                ):
                    session.run(
                        options,
                        sourceZipStream=requestZipStream,
                        responseZipStream=responseZipStream,
                        logFilters=[],
                    )
                    result = ArelleProcessingResult.fromSession(session)
                assert requestZipStream.closed, "Forgot to close the stream."
                return result
            except Exception as arelle_exception:
                message = "Exception encountered while calling Arelle for report."
                L.exception(message, exc_info=arelle_exception)
                raise ArelleRelatedException(message) from arelle_exception

    def _makeOptions(
        self,
        *,
        calcs: str = "c11r",
        plugins: Optional[str] = None,
        pluginOptions: Optional[dict] = None,
    ) -> RuntimeOptions:
        """RuntimeOptions shared by all report processing: validation on
        (calcs 1.1 round-to-nearest unless overridden, UTR, inconsistent
        duplicate facts warned) and logging to buffer."""
        return RuntimeOptions(
            internetConnectivity="offline" if self.workOffline else "online",
            keepOpen=True,
            logFile="logToBuffer",
            logFormat="%(message)s",
            logPropagate=False,
            packages=[str(t) for t in self.taxonomyPackages],
            plugins=plugins,
            pluginOptions=pluginOptions if pluginOptions is not None else {},
            validate=True,
            calcs=calcs,
            utrValidate=True,
            validateDuplicateFacts="inconsistent",
            showOptions=False,
        )

    def validateReportPackage(
        self, source: FilelikeAndFileName, *, disableCalculationValidation: bool = False
    ) -> ArelleProcessingResult:
        options = self._makeOptions(
            calcs="none" if disableCalculationValidation else "c11r",
        )
        return self._run(source, options)

    def generateXBRLJson(self, source: FilelikeAndFileName) -> ArelleProcessingResult:
        options = self._makeOptions(
            plugins="saveLoadableOIM",
            pluginOptions={"saveLoadableOIM": "report.json"},
        )
        jsonBytesIO = BytesIO()
        result = self._run(source, options, jsonBytesIO)
        try:
            json = _singleFileFromResponseZip(
                jsonBytesIO, "Arelle xBRL JSON generation"
            )
            jsonFilename = PurePath(source.filename).with_suffix(".json").name
            result._xbrlJson = FilelikeAndFileName(
                fileContent=json, filename=jsonFilename
            )
        except Exception as e:
            result.addException(e)
        return result

    def generateInlineViewer(
        self, source: FilelikeAndFileName
    ) -> ArelleProcessingResult:
        viewerBytesIO = BytesIO()
        options = self._makeOptions(
            plugins="ixbrl-viewer",
            pluginOptions={
                "saveViewerDest": viewerBytesIO,
                "viewer_feature_review": False,
                "validationMessages": True,
                "viewer_feature_highlight_facts_on_startup": False,
                "useStubViewer": False,
                "viewerNoCopyScript": True,
                "viewerURL": ARELLE_VIEWER_URL,
            },
        )
        result = self._run(source, options)
        try:
            viewer = _singleFileFromResponseZip(viewerBytesIO, "Arelle & inline-viewer")
            viewerFilename = f"{PurePath(source.filename).stem}_viewer.html"
            result._viewer = FilelikeAndFileName(
                fileContent=viewer, filename=viewerFilename
            )
        except Exception as e:
            result.addException(
                e,
                message="Exception encountered during processing of Arelle's response stream",
            )
        return result

    @staticmethod
    def getTaxonomyPackagesFromDir(
        taxonomyPackageDir: str | Path | None,
    ) -> list[Path]:
        if taxonomyPackageDir is None:
            return []

        if isinstance(taxonomyPackageDir, (str, Path)):
            tdir = Path(taxonomyPackageDir)
        else:
            raise ArelleRelatedException(
                f"Supplied {taxonomyPackageDir=} needs to be a string or Path."
            )

        taxonomyPackages: list[Path] = []
        for candidate in tdir.glob("**/*.zip"):
            if candidate.is_file():
                taxonomyPackages.append(candidate)
        if not taxonomyPackages:
            raise ArelleRelatedException(
                f"Supplied {taxonomyPackageDir=} does not contain any taxonomy packages."
            )
        return taxonomyPackages

    @staticmethod
    def _determineViewerUrl() -> str:
        try:
            viewer_version = version("ixbrl-viewer")
            viewer_url_cdn_base = r"https://cdn.jsdelivr.net/npm/ixbrl-viewer@<version>/iXBRLViewerPlugin/viewer/dist/ixbrlviewer.js"
            viewer_url = viewer_url_cdn_base.replace("<version>", viewer_version)
            return viewer_url
        except PackageNotFoundError:
            an_old_viewer_url = r"https://cdn.jsdelivr.net/npm/ixbrl-viewer@1.4.60/iXBRLViewerPlugin/viewer/dist/ixbrlviewer.js"
            return an_old_viewer_url

    @staticmethod
    def _versionInformation() -> ArelleVersionHolder:
        def makeVersionInformation(distribution: str) -> VersionInformationTuple:
            fallback = VersionInformationTuple(distribution, "<unknown>")
            try:
                meta = metadata(distribution)
                a = meta.get_all("Name")
                b = meta.get_all("Version")
                if a and b:
                    return VersionInformationTuple(
                        name=next(iter(meta.get_all("Name", []))),
                        version=next(iter(meta.get_all("Version", []))),
                    )
            except Exception as e:
                L.exception(
                    "Failed to parse Arelle and Arelle ixbrl-viewer metadata",
                    exc_info=e,
                )
            return fallback

        return ArelleVersionHolder(
            arelle=makeVersionInformation("arelle-release"),
            ixbrlViewer=makeVersionInformation("ixbrl-viewer"),
        )


ARELLE_VERSION_INFORMATION = ArelleReportProcessor._versionInformation()
ARELLE_VIEWER_URL = ArelleReportProcessor._determineViewerUrl()


def getOrCreateReportPackage(reportPackage: Path) -> FilelikeAndFileName:
    """Return the given zip as-is, or wrap a single inline report document in an
    unconstrained report package."""
    if not isinstance(reportPackage, Path):
        raise ArelleRelatedException(
            f"Passed a report package {reportPackage=} that is not a Path"
        )

    zipName = reportPackage.name
    if zipfile.is_zipfile(reportPackage):
        with open(reportPackage, "rb") as zin:
            bytes = zin.read()
    elif reportPackage.suffix in {".xhtml", ".html", ".htm"}:
        with BytesIO() as write_bio:
            with zipfile.ZipFile(write_bio, "w") as z:
                z.write(reportPackage, f"a/reports/{reportPackage.name}")
                z.writestr(
                    zinfo_or_arcname="a/META-INF/reportPackage.json",
                    data=UNCONSTRAINED_REPORT_PACKAGE_JSON,
                )
            bytes = write_bio.getvalue()
        zipName = reportPackage.with_suffix(".zip").name
    else:
        raise ArelleRelatedException(
            f"Passed a {reportPackage=} that has an unrecognised file type."
        )
    return FilelikeAndFileName(fileContent=bytes, filename=zipName)
