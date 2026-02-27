import logging
import threading
import zipfile
from contextlib import closing
from importlib.metadata import PackageNotFoundError, metadata, version
from io import BytesIO
from pathlib import Path, PurePath
from typing import BinaryIO, Optional

from arelle import PackageManager, PluginManager
from arelle.api.Session import Session
from arelle.CntlrCmdLine import RuntimeOptions
from arelle.logging.handlers.LogToXmlHandler import LogToXmlHandler

from mireport.arelle.support import (
    ArelleProcessingResult,
    ArelleRelatedException,
    ArelleVersionHolder,
)
from mireport.filesupport import FilelikeAndFileName
from mireport.version import VersionInformationTuple
from mireport.xbrlreport import UNCONSTRAINED_REPORT_PACKAGE_JSON

BIG_ARELLE_LOCK = threading.Lock()

L = logging.getLogger(__name__)


class ArelleReportProcessor:
    """Wrapper around the Arelle Session() API for the various validations and plugins wanted."""

    def __init__(
        self,
        *,
        taxonomyPackages: Optional[list[Path]] = None,
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
        responseZipStream: Optional[BinaryIO] = None,
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
                    pass
                with (
                    Session() as session,
                    closing(LogToXmlHandler()) as logHandler,
                    reportPackage.fileLike() as requestZipStream,
                ):
                    session.run(
                        options,
                        sourceZipStream=requestZipStream,
                        responseZipStream=responseZipStream,
                        logHandler=logHandler,
                        logFilters=[],
                    )
                    result = ArelleProcessingResult.fromLogToXmlHandler(logHandler)
                assert requestZipStream.closed, "Forgot to close the stream."
                return result
            except Exception as arelle_exception:
                message = "Exception encountered while calling Arelle for report."
                L.exception(message, exc_info=arelle_exception)
                raise ArelleRelatedException(message) from arelle_exception

    def validateReportPackage(
        self, source: FilelikeAndFileName, *, disableCalculationValidation: bool = False
    ) -> ArelleProcessingResult:
        # Use Calc 1.1 round to nearest "c11r" for calculation validation unless
        # calculation validation is disabled.
        if disableCalculationValidation:
            calcs = "none"
        else:
            calcs = "c11r"

        validationOptions = RuntimeOptions(
            internetConnectivity="offline" if self.workOffline is True else "online",
            keepOpen=True,
            logFormat="%(asctime)s [%(messageCode)s] %(message)s - %(file)s",
            logPropagate=False,
            packages=[str(t) for t in self.taxonomyPackages],
            # You have to specify a plugin to avoid specifying an entryPointFile. We
            # don't want to specify and entryPointFile as we pass the zipStream in
            # later. saveLoadableOIM is used as a "null" plugin here to passify Arelle.
            plugins="saveLoadableOIM",
            pluginOptions={},
            # Turn validation on
            validate=True,
            calcs=calcs,
            # Validate against the unit type registry
            utrValidate=True,
            # Warn if inconsistent duplicate facts encountered
            validateDuplicateFacts="inconsistent",
            showOptions=False,
        )
        return self._run(source, validationOptions)

    def generateXBRLJson(self, source: FilelikeAndFileName) -> ArelleProcessingResult:
        filename = "foo.json"
        jsonOptions = RuntimeOptions(
            internetConnectivity="offline" if self.workOffline else "online",
            keepOpen=True,
            logFormat="%(asctime)s [%(messageCode)s] %(message)s - %(file)s",
            logPropagate=False,
            packages=[str(t) for t in self.taxonomyPackages],
            plugins="saveLoadableOIM",
            pluginOptions={
                "saveLoadableOIM": filename,
            },
            # Turn validation on
            validate=True,
            # Use Calc 1.1 round to nearest "c11r" for calculation validation
            calcs="c11r",
            # Validate against the unit type registry
            utrValidate=True,
            # Warn if inconsistent duplicate facts encountered
            validateDuplicateFacts="inconsistent",
            showOptions=False,
        )

        jsonBytesIO = BytesIO()
        result = self._run(source, jsonOptions, jsonBytesIO)
        jsonBytesIO.seek(0)
        try:
            with zipfile.ZipFile(jsonBytesIO, "r") as zf:
                a = zf.infolist()
                assert len(a) == 1, (
                    f"Arelle xBRL JSON generation has gone wrong. Zip contents: {zf.namelist()}"
                )
                json = zf.read(a[0])
        except Exception as e:
            result.addException(e)
            raise
        finally:
            del jsonBytesIO

        jsonFilename = PurePath(source.filename).with_suffix(".json").name
        result._xbrlJson = FilelikeAndFileName(fileContent=json, filename=jsonFilename)
        return result

    def generateInlineViewer(
        self, source: FilelikeAndFileName
    ) -> ArelleProcessingResult:
        viewerBytesIO = BytesIO()
        viewer_plugin_options = {
            "saveViewerDest": viewerBytesIO,
            "viewer_feature_review": False,
            "validationMessages": True,
            "viewerNoCopyScript": True,
            "viewer_feature_highlight_facts_on_startup": True,
            "useStubViewer": False,
            "viewerURL": ARELLE_VIEWER_URL,
        }

        viewerOptions = RuntimeOptions(
            internetConnectivity="offline" if self.workOffline else "online",
            keepOpen=True,
            logFormat="%(asctime)s [%(messageCode)s] %(message)s - %(file)s",
            logPropagate=False,
            packages=[str(t) for t in self.taxonomyPackages],
            pluginOptions=viewer_plugin_options,
            plugins="ixbrl-viewer",
            # Turn validation on
            validate=True,
            # Use Calc 1.1 round to nearest "c11r" for calculation validation
            calcs="c11r",
            # Validate against the unit type registry
            utrValidate=True,
            # Warn if inconsistent duplicate facts encountered
            validateDuplicateFacts="inconsistent",
            showOptions=False,
        )
        result = self._run(source, viewerOptions)
        viewerBytesIO.seek(0)
        try:
            with zipfile.ZipFile(viewerBytesIO, "r") as zf:
                a = zf.infolist()
                assert len(a) == 1, (
                    f"Arelle & inline-viewer has gone wrong. Zip contents: {zf.namelist()}"
                )
                viewer = zf.read(a[0])
        except Exception as e:
            result.addException(
                e,
                message="Exception encountered during processing of Arelle's response stream",
            )
        finally:
            del viewerBytesIO

        viewerFilename = f"{PurePath(source.filename).stem}_viewer.html"
        result._viewer = FilelikeAndFileName(
            fileContent=viewer, filename=viewerFilename
        )
        return result

    @staticmethod
    def getTaxonomyPackagesFromDir(
        taxonomyPackageDir: Optional[str | Path],
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
    """"""
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
