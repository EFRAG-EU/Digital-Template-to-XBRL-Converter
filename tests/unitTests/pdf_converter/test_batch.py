"""Batch PDF conversion policy, shared by the CLI and the web app.

The policy is the interesting part and is asserted directly here rather than
only through either front end: the original always ships, failures are warnings
and never errors, and a missing binary is reported once for the batch.
"""

from __future__ import annotations

from typing import Any

import pytest

from mireport.conversionresults import (
    ConversionResultsBuilder,
    Severity,
)
from mireport.filesupport import FilelikeAndFileName
from mireport.pdf_converter import (
    BACKENDS_IN_PREFERENCE_ORDER,
    PdfConversionError,
    PdfToolNotFoundError,
    ResolvedConverter,
    _batch,
    convertSupplementaryPdfs,
)


def makePdf(name: str) -> FilelikeAndFileName:
    return FilelikeAndFileName(fileContent=b"%PDF-1.7", filename=name)


def converted(name: str) -> FilelikeAndFileName:
    return FilelikeAndFileName(fileContent=b"<html/>", filename=name)


@pytest.fixture
def results() -> ConversionResultsBuilder:
    return ConversionResultsBuilder()


@pytest.fixture
def pc(results: ConversionResultsBuilder) -> Any:
    with results.processingContext("test") as context:
        yield context


FAKE_CONVERTER = ResolvedConverter(
    BACKENDS_IN_PREFERENCE_ORDER[0], "/usr/bin/pdf2htmlEX"
)


@pytest.fixture
def workingConverter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_batch, "findConverter", lambda: FAKE_CONVERTER)
    monkeypatch.setattr(
        _batch,
        "convertPdfToHtml",
        lambda content, *, filename, converter: converted(
            filename.replace(".pdf", ".xhtml")
        ),
    )


@pytest.fixture
def noBinary(monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser() -> str:
        raise PdfToolNotFoundError("pdf2htmlEX was not found")

    monkeypatch.setattr(_batch, "findConverter", raiser)


def warnings(results: ConversionResultsBuilder) -> list[str]:
    return [
        m.messageText
        for m in results.build().messages
        if m.severity is Severity.WARNING
    ]


# ── nothing to do ─────────────────────────────────────────────────────────────


class TestNoPdfs:
    def test_returns_two_empty_lists(self, results: Any, pc: Any) -> None:
        assert convertSupplementaryPdfs([], results, pc) == ([], [])

    def test_says_nothing_alarming(self, results: Any, pc: Any) -> None:
        """The processing context emits its own progress marks, so this is
        about warnings and errors, not about total silence."""
        convertSupplementaryPdfs([], results, pc)
        assert warnings(results) == []
        assert results.conversionSuccessful

    def test_does_not_look_for_the_binary(
        self, results: Any, pc: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No PDFs means no reason to complain about a missing converter."""

        def boom() -> str:
            raise AssertionError("should not have looked for the binary")

        monkeypatch.setattr(_batch, "findConverter", boom)
        convertSupplementaryPdfs([], results, pc)


# ── the happy path ────────────────────────────────────────────────────────────


class TestConversionWorks:
    def test_attaches_the_originals(
        self, results: Any, pc: Any, workingConverter: None
    ) -> None:
        pdfs = [makePdf("a.pdf"), makePdf("b.pdf")]
        assert convertSupplementaryPdfs(pdfs, results, pc).attachments == pdfs

    def test_produces_a_member_per_pdf(
        self, results: Any, pc: Any, workingConverter: None
    ) -> None:
        result = convertSupplementaryPdfs(
            [makePdf("a.pdf"), makePdf("b.pdf")], results, pc
        )
        assert [m.filename for m in result.docsetMembers] == ["a.xhtml", "b.xhtml"]

    def test_preserves_order(
        self, results: Any, pc: Any, workingConverter: None
    ) -> None:
        """Document set order is presentation order, so it must be the upload's."""
        pdfs = [makePdf(n) for n in ("z.pdf", "a.pdf", "m.pdf")]
        result = convertSupplementaryPdfs(pdfs, results, pc)
        assert [m.filename for m in result.docsetMembers] == [
            "z.xhtml",
            "a.xhtml",
            "m.xhtml",
        ]

    def test_says_nothing_alarming(
        self, results: Any, pc: Any, workingConverter: None
    ) -> None:
        convertSupplementaryPdfs([makePdf("a.pdf")], results, pc)
        assert warnings(results) == []
        assert results.conversionSuccessful


# ── no converter installed ────────────────────────────────────────────────────


class TestNoConverterInstalled:
    def test_originals_are_still_attached(
        self, results: Any, pc: Any, noBinary: None
    ) -> None:
        """The reason for attaching the PDF as well as converting it."""
        pdfs = [makePdf("a.pdf"), makePdf("b.pdf")]
        assert convertSupplementaryPdfs(pdfs, results, pc).attachments == pdfs

    def test_no_members(self, results: Any, pc: Any, noBinary: None) -> None:
        result = convertSupplementaryPdfs([makePdf("a.pdf")], results, pc)
        assert result.docsetMembers == []

    def test_warns_once_for_the_batch(
        self, results: Any, pc: Any, noBinary: None
    ) -> None:
        """Not once per file — three uploads must not mean three identical
        complaints about the deployment."""
        pdfs = [makePdf(f"{n}.pdf") for n in "abcde"]
        convertSupplementaryPdfs(pdfs, results, pc)
        assert len(warnings(results)) == 1

    def test_conversion_still_succeeds(
        self, results: Any, pc: Any, noBinary: None
    ) -> None:
        convertSupplementaryPdfs([makePdf("a.pdf")], results, pc)
        assert results.conversionSuccessful


# ── individual failures ───────────────────────────────────────────────────────


class TestPartialFailure:
    @pytest.fixture
    def flakyConverter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_batch, "findConverter", lambda: FAKE_CONVERTER)

        def convert(
            content: bytes, *, filename: str, converter: ResolvedConverter
        ) -> FilelikeAndFileName:
            if filename == "bad.pdf":
                raise PdfConversionError("that one is corrupt")
            return converted(filename.replace(".pdf", ".xhtml"))

        monkeypatch.setattr(_batch, "convertPdfToHtml", convert)

    def test_good_pdfs_still_convert(
        self, results: Any, pc: Any, flakyConverter: None
    ) -> None:
        result = convertSupplementaryPdfs(
            [makePdf("a.pdf"), makePdf("bad.pdf"), makePdf("b.pdf")], results, pc
        )
        assert [m.filename for m in result.docsetMembers] == ["a.xhtml", "b.xhtml"]

    def test_the_failed_pdf_is_still_attached(
        self, results: Any, pc: Any, flakyConverter: None
    ) -> None:
        result = convertSupplementaryPdfs(
            [makePdf("a.pdf"), makePdf("bad.pdf")], results, pc
        )
        assert [a.filename for a in result.attachments] == ["a.pdf", "bad.pdf"]

    def test_warns_once_naming_the_file(
        self, results: Any, pc: Any, flakyConverter: None
    ) -> None:
        convertSupplementaryPdfs([makePdf("a.pdf"), makePdf("bad.pdf")], results, pc)
        assert len(warnings(results)) == 1
        assert "bad.pdf" in warnings(results)[0]

    def test_conversion_still_succeeds(
        self, results: Any, pc: Any, flakyConverter: None
    ) -> None:
        """A warning must not clear conversionSuccessful."""
        convertSupplementaryPdfs([makePdf("bad.pdf")], results, pc)
        assert results.conversionSuccessful

    def test_every_pdf_failing_is_still_not_an_error(
        self, results: Any, pc: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_batch, "findConverter", lambda: FAKE_CONVERTER)

        def boom(*args: Any, **kwargs: Any) -> FilelikeAndFileName:
            raise PdfConversionError("nope")

        monkeypatch.setattr(_batch, "convertPdfToHtml", boom)
        result = convertSupplementaryPdfs(
            [makePdf("a.pdf"), makePdf("b.pdf")], results, pc
        )
        assert result.docsetMembers == []
        assert len(result.attachments) == 2
        assert results.conversionSuccessful
