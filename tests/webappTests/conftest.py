import io
import zipfile
from pathlib import Path

import pytest
from cachelib.file import FileSystemCache
from tests.offline_arelle import taxonomyPackagesDir

from digital_converter_webapp import create_app
from digital_converter_webapp.conversion import Conversion, ConversionStore
from mireport.arelle.report_info import ArelleReportProcessor
from mireport.arelle.support import ArelleProcessingResult
from mireport.conversionresults import Message, MessageType, Severity
from mireport.filesupport import FilelikeAndFileName
from mireport.html_to_xhtml import documentToBytes, normaliseDocument, parseDocument
from mireport.pdf_converter import ANNEX_LABELS, PdfToolNotFoundError

SAMPLE_XLSX = (
    Path(__file__).parent.parent / "data" / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"

# Normalised the way production does it: the empty ix:header the normaliser
# injects is what makes the file an Inline XBRL Document, and so a document set
# member, rather than one dropped as "Non-inline file is not loadable".
CONVERTED_PDF = FilelikeAndFileName(
    fileContent=documentToBytes(
        normaliseDocument(
            parseDocument(
                b"<html><head><title>a</title></head><body><p>annex</p></body></html>"
            ),
            ANNEX_LABELS,
        )
    ),
    filename="annex1.xhtml",
)


def _test_config(session_dir, **overrides):
    return {
        "TESTING": True,
        "SESSION_CACHELIB": FileSystemCache(str(session_dir)),
        "SECRET_KEY": "test-secret",
        "TAXONOMY_PACKAGE_DIR": taxonomyPackagesDir(),
        # The results page complete when first shown, as these tests expect.
        "RUN_STEPS_INLINE": True,
        # Most tests look into the sections; a deployment gets the flat table.
        "RESULTS_LAYOUT": "sections",
        **overrides,
    }


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    # Supplementary PDFs are off unless a deployment opts in; the suite opts in.
    return create_app(
        _test_config(
            tmp_path_factory.mktemp("sessions"), ENABLE_SUPPLEMENTARY_PDFS=True
        )
    )


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def captcha_app(tmp_path_factory):
    return create_app(
        _test_config(tmp_path_factory.mktemp("captcha-sessions"), ENABLE_CAPTCHA=True)
    )


@pytest.fixture()
def captcha_client(captcha_app):
    return captcha_app.test_client()


@pytest.fixture(scope="session")
def no_pdfs_app(tmp_path_factory):
    return create_app(
        _test_config(
            tmp_path_factory.mktemp("no-pdf-sessions"), ENABLE_SUPPLEMENTARY_PDFS=False
        )
    )


@pytest.fixture()
def no_pdfs_client(no_pdfs_app):
    return no_pdfs_app.test_client()


def postWorkbook(client, *, filename="test.xlsx", data=None, pdfs=None, **form):
    """POST /upload with an xlsx (the sample workbook unless ``data`` is given),
    optional ``pdfs`` parts and any extra form fields."""
    payload = SAMPLE_XLSX.read_bytes() if data is None else data
    fields = {"file": (io.BytesIO(payload), filename)}
    if pdfs is not None:
        fields["pdfs"] = pdfs
    fields.update(form)
    return client.post("/upload", data=fields, content_type="multipart/form-data")


@pytest.fixture()
def sample_xlsx() -> bytes:
    return SAMPLE_XLSX.read_bytes()


@pytest.fixture()
def upload():
    """``upload(client, filename=, data=, pdfs=, **form)`` -> the /upload response."""
    return postWorkbook


@pytest.fixture()
def upload_id(upload):
    """Upload the sample workbook and return the conversion id (no conversion
    is triggered)."""

    def _uploadId(client) -> str:
        resp = upload(client)
        assert resp.status_code == 303
        return resp.headers["Location"].rstrip("/").split("/")[-1]

    return _uploadId


@pytest.fixture()
def make_pdf():
    """``make_pdf(name, content)`` -> a (stream, filename) part for the pdfs field."""

    def _makePdf(name="annex1.pdf", content=PDF_BYTES):
        return (io.BytesIO(content), name)

    return _makePdf


@pytest.fixture()
def pdf_bytes() -> bytes:
    return PDF_BYTES


@pytest.fixture()
def conversion_of():
    """``conversion_of(client)`` -> the one conversion in the client's session."""

    def _conversionOf(client) -> Conversion:
        with client.session_transaction() as sess:
            (conv,) = ConversionStore(sess).active().values()
        return conv

    return _conversionOf


@pytest.fixture()
def workbookParses(monkeypatch) -> list[bool]:
    """Every workbook parse from here on, each recorded as whether it was read-only."""
    from mireport.xlsx_template_reader import util

    parses: list[bool] = []
    parse = util.load_workbook

    def _counting(*args, **kwargs):
        parses.append(kwargs.get("read_only", False))
        return parse(*args, **kwargs)

    monkeypatch.setattr(util, "load_workbook", _counting)
    return parses


@pytest.fixture(scope="session")
def converted_id(app):
    """Run the full pipeline once and return ``(conversion id, client)``.

    Slow because it runs Arelle validation, hence session scoped: every module
    that wants a finished conversion shares this one. Uses a plain test_client
    (no context manager) to avoid Flask's preserve_context mode, which causes
    "Popped wrong request context" errors when multiple requests share the same
    client.
    """
    c = app.test_client()
    resp = postWorkbook(c)
    conv_id = resp.headers["Location"].rstrip("/").split("/")[-1]
    # Trigger the conversion (stores results in the server-side session)
    c.get(f"/conversions/{conv_id}")
    return conv_id, c


def patchConverterPresent(monkeypatch):
    """pdf2htmlEX present and working. Patched on mireport.pdf_converter._batch,
    where the conversion policy actually lives — the web app only supplies the
    uploaded files and consumes the result."""
    from mireport.pdf_converter import (
        BACKENDS_IN_PREFERENCE_ORDER,
        ResolvedConverter,
        _batch,
    )

    monkeypatch.setattr(
        _batch,
        "findConverter",
        lambda: ResolvedConverter(
            BACKENDS_IN_PREFERENCE_ORDER[0], "/usr/bin/pdf2htmlEX"
        ),
    )
    monkeypatch.setattr(
        _batch,
        "convertPdfToHtml",
        lambda content, *, filename, converter: FilelikeAndFileName(
            fileContent=CONVERTED_PDF.fileContent,
            filename=f"{Path(filename).stem}.xhtml",
        ),
    )


@pytest.fixture
def convertedOk(monkeypatch):
    patchConverterPresent(monkeypatch)


def patchNoBinary(monkeypatch):
    """No PDF converter installed."""
    from mireport.pdf_converter import _batch

    def raiser():
        raise PdfToolNotFoundError("pdf2htmlEX was not found")

    monkeypatch.setattr(_batch, "findConverter", raiser)


@pytest.fixture
def noBinary(monkeypatch):
    patchNoBinary(monkeypatch)


@pytest.fixture(scope="module")
def viewed_docset(app):
    """``(client, conversion id)`` for one real conversion of the sample workbook
    with a supplementary PDF, viewer already generated.

    Each conversion runs Arelle (~13s), so tests that only read the result share
    one per module. Module scoped, so ``monkeypatch`` is unavailable and a
    ``MonkeyPatch.context()`` stands in.
    """
    client = app.test_client()
    with pytest.MonkeyPatch.context() as monkeypatch:
        patchConverterPresent(monkeypatch)
        postWorkbook(client, pdfs=[(io.BytesIO(PDF_BYTES), "annex1.pdf")])
        with client.session_transaction() as sess:
            (conversion,) = ConversionStore(sess).active().values()
        client.get(f"/conversions/{conversion.id}")
        client.get(f"/downloadFile/{conversion.id}/viewer/")
    return client, conversion.id


def _packageNamesOfOneConversion(app, pdfs, *patches) -> list[str]:
    """The report package's member names after converting the sample workbook
    with ``pdfs``, Arelle skipped and ``patches`` applied. For module scoped
    fixtures, so ``MonkeyPatch.context()`` stands in for ``monkeypatch``."""
    client = app.test_client()
    with pytest.MonkeyPatch.context() as monkeypatch:
        patchArelleOut(monkeypatch)
        for patch in patches:
            patch(monkeypatch)
        postWorkbook(client, pdfs=pdfs)
        with client.session_transaction() as sess:
            (conversion,) = ConversionStore(sess).active().values()
        client.get(f"/conversions/{conversion.id}")
        with client.session_transaction() as sess:
            conversion = ConversionStore(sess).active()[conversion.id]
    assert conversion.zip is not None
    with zipfile.ZipFile(io.BytesIO(conversion.zip.fileContent)) as zf:
        return zf.namelist()


@pytest.fixture(scope="module")
def convertedPdfPackage(app) -> list[str]:
    """Package member names of one conversion with a PDF that converts, shared by
    the tests that only read the package layout."""
    return _packageNamesOfOneConversion(
        app, [(io.BytesIO(PDF_BYTES), "annex1.pdf")], patchConverterPresent
    )


@pytest.fixture(scope="module")
def unconvertedPdfPackage(app) -> list[str]:
    """As ``convertedPdfPackage``, with no PDF converter installed."""
    return _packageNamesOfOneConversion(
        app, [(io.BytesIO(PDF_BYTES), "annex1.pdf")], patchNoBinary
    )


class FakeArelleProcessingResult(ArelleProcessingResult):
    """Stands in for a real Arelle validation in tests that never look at it.

    Carries an XBRL-validation ERROR saying so, so a test that comes to depend
    on the validation outcome (the results-page banner, the downloads) fails
    loudly rather than passing against a validation that never ran. The
    conversion still succeeds, but the ERROR withholds the report: every
    download of it is refused and the viewer isn't built.
    """

    def __init__(self) -> None:
        super().__init__()
        self._validationMessages.append(
            Message(
                messageText="Arelle validation was not run (test stub)",
                severity=Severity.ERROR,
                messageType=MessageType.XbrlValidation,
            )
        )


def patchArelleOut(monkeypatch):
    """Both of a conversion's Arelle validations, of the report package and of
    the xBRL-JSON, stubbed out."""
    for validation in ("validateReportPackage", "validateXbrlJson"):
        monkeypatch.setattr(
            ArelleReportProcessor,
            validation,
            lambda self, source, **kwargs: FakeArelleProcessingResult(),
        )


@pytest.fixture
def skipArelle(monkeypatch):
    """Skip the several-second Arelle validations in a conversion the test only
    inspects for package layout, attachments or PDF warnings."""
    patchArelleOut(monkeypatch)
