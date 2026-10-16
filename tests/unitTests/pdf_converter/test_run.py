"""Tests for finding and running a PDF-to-HTML converter.

Two complementary styles, because neither alone is enough:

- against the stub executables (``stubBinary``), which really fork a process, so
  exit codes, timeouts, argument quoting and missing output files behave as they
  will in production. Parametrised over every supported backend via the
  ``backend`` fixture, so adding one gets it covered here automatically;
- against a monkeypatched ``subprocess.run``, for assertions about the argv we
  construct that would be tedious to make from the far side of a process.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from mireport.exceptions import MIReportException
from mireport.pdf_converter import (
    BACKENDS_IN_PREFERENCE_ORDER,
    PATH_ENV_VAR,
    PdfConversionError,
    PdfToolNotFoundError,
    availableConverters,
    convertPdfToHtml,
    findConverter,
)
from mireport.pdf_converter._backends import (
    Pdf2HtmlExBackend,
    PdfConverterBackend,
    PdfToHtmlBackend,
)
from mireport.pdf_converter._run import runConverter

# ── backend registry ──────────────────────────────────────────────────────────


class TestBackendRegistry:
    def test_pdf2htmlex_is_preferred(self) -> None:
        """It reproduces the PDF's own fonts and keeps text selectable;
        pdftohtml renders each page to a bitmap."""
        assert isinstance(BACKENDS_IN_PREFERENCE_ORDER[0], Pdf2HtmlExBackend)

    def test_pdftohtml_is_the_fallback(self) -> None:
        assert isinstance(BACKENDS_IN_PREFERENCE_ORDER[-1], PdfToHtmlBackend)

    def test_names_are_unique(self) -> None:
        names = [b.name for b in BACKENDS_IN_PREFERENCE_ORDER]
        assert len(names) == len(set(names))

    def test_every_backend_is_fully_described(self) -> None:
        for b in BACKENDS_IN_PREFERENCE_ORDER:
            assert b.name and b.executable and b.description


# ── locating a converter ──────────────────────────────────────────────────────


class TestFindConverter:
    def test_finds_an_explicit_path(self, stubBinary: Path) -> None:
        assert Path(findConverter(str(stubBinary)).binaryPath) == stubBinary

    def test_backend_is_inferred_from_the_executable_name(
        self, stubBinary: Path, backend: PdfConverterBackend
    ) -> None:
        """So an install outside PATH is still driven with the right flags."""
        assert findConverter(str(stubBinary)).backend.name == backend.name

    def test_explicit_backend_overrides_inference(self, stubBinaries: dict) -> None:
        resolved = findConverter(
            str(stubBinaries["pdftohtml"]), backendName="pdf2htmlEX"
        )
        assert resolved.backend.name == "pdf2htmlEX"

    def test_raises_when_absent(self) -> None:
        with pytest.raises(PdfToolNotFoundError):
            findConverter("definitely-not-a-real-binary-8f3a1c")

    def test_names_what_it_looked_for(self) -> None:
        with pytest.raises(PdfToolNotFoundError, match="no-such-tool-8f3a1c"):
            findConverter("no-such-tool-8f3a1c")

    def test_unknown_backend_name_is_rejected(self) -> None:
        with pytest.raises(PdfToolNotFoundError, match="nosuchconverter"):
            findConverter(backendName="nosuchconverter")

    def test_prefers_the_best_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mireport.pdf_converter import _run

        monkeypatch.setattr(_run.shutil, "which", lambda name: f"/usr/bin/{name}")
        assert findConverter().backend.name == BACKENDS_IN_PREFERENCE_ORDER[0].name

    def test_falls_back_when_the_preferred_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mireport.pdf_converter import _run

        monkeypatch.setattr(
            _run.shutil,
            "which",
            lambda name: "/usr/bin/pdftohtml" if name == "pdftohtml" else None,
        )
        assert findConverter().backend.name == "pdftohtml"

    def test_no_converter_at_all_names_every_candidate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message is a deployment instruction, so it must say what to install."""
        from mireport.pdf_converter import _run

        monkeypatch.setattr(_run.shutil, "which", lambda name: None)
        with pytest.raises(PdfToolNotFoundError) as raised:
            findConverter()
        for b in BACKENDS_IN_PREFERENCE_ORDER:
            assert b.executable in str(raised.value)


class TestConfiguredByEnvironment:
    """One variable, so the CLI and the web app converge on the same binary."""

    def test_the_environment_names_the_converter(
        self, stubBinary: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PATH_ENV_VAR, str(stubBinary))
        assert Path(findConverter().binaryPath) == stubBinary

    def test_an_explicit_path_still_wins(
        self, stubBinary: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PATH_ENV_VAR, "definitely-not-a-real-binary-8f3a1c")
        assert Path(findConverter(str(stubBinary)).binaryPath) == stubBinary

    def test_an_empty_value_is_not_a_configured_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unset and set-to-nothing mean the same thing in a .env file."""
        from mireport.pdf_converter import _run

        monkeypatch.setenv(PATH_ENV_VAR, "")
        monkeypatch.setattr(_run.shutil, "which", lambda name: f"/usr/bin/{name}")
        assert findConverter().backend.name == BACKENDS_IN_PREFERENCE_ORDER[0].name

    def test_a_wrong_path_is_reported_not_silently_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mireport.pdf_converter import _run

        monkeypatch.setenv(PATH_ENV_VAR, "no-such-tool-8f3a1c")
        monkeypatch.setattr(_run.shutil, "which", lambda name: None)
        with pytest.raises(PdfToolNotFoundError, match="no-such-tool-8f3a1c"):
            findConverter()


class TestAvailableConverters:
    def test_empty_when_nothing_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mireport.pdf_converter import _run

        monkeypatch.setattr(_run.shutil, "which", lambda name: None)
        assert availableConverters() == []

    def test_reports_all_present_best_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mireport.pdf_converter import _run

        monkeypatch.setattr(_run.shutil, "which", lambda name: f"/usr/bin/{name}")
        assert [c.backend.name for c in availableConverters()] == [
            b.name for b in BACKENDS_IN_PREFERENCE_ORDER
        ]


# ── the exception hierarchy ───────────────────────────────────────────────────


class TestExceptions:
    def test_tool_not_found_is_a_conversion_error(self) -> None:
        """Callers that only care whether conversion worked catch one type."""
        assert issubclass(PdfToolNotFoundError, PdfConversionError)

    def test_rooted_in_the_project_hierarchy(self) -> None:
        assert issubclass(PdfConversionError, MIReportException)


# ── real subprocess round trip, per backend ───────────────────────────────────


class TestAgainstStubBinary:
    def test_returns_converted_html(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        behaviour("ok")
        result = convertPdfToHtml(
            pdfBytes, filename="annex1.pdf", binaryPath=str(stubBinary)
        )
        assert b"Converted annex" in result.fileContent

    def test_filename_is_derived_from_the_pdf(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        behaviour("ok")
        result = convertPdfToHtml(
            pdfBytes, filename="annex1.pdf", binaryPath=str(stubBinary)
        )
        assert result.filename == "annex1.xhtml"

    def test_output_is_self_contained(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        """pdftohtml writes page images beside the HTML; they have to be inlined
        before the working directory disappears, or the member ships broken."""
        behaviour("ok")
        result = convertPdfToHtml(
            pdfBytes, filename="a.pdf", binaryPath=str(stubBinary)
        )
        assert b".png" not in result.fileContent
        assert b"data:image/png;base64," in result.fileContent

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("annex 1.pdf", "annex_1.xhtml"),
            ("../escape.pdf", "escape.xhtml"),
            ("sub/dir/annex.pdf", "annex.xhtml"),
            ("back\\slash.pdf", "slash.xhtml"),
            ("wéird–name.pdf", "wéird_name.xhtml"),
            ("UPPER.PDF", "UPPER.xhtml"),
            ("no_extension", "no_extension.xhtml"),
            # zipSafeString keeps only word characters and dots, so a hyphen
            # becomes an underscore. Shared with entity names rather than
            # forked for PDFs, so filenames sanitise the one familiar way.
            ("annual-report.pdf", "annual_report.xhtml"),
            (".pdf", "pdf.xhtml"),
            ("...", "annex.xhtml"),
        ],
        ids=[
            "space",
            "dotdot",
            "path",
            "backslash",
            "non-ascii",
            "upper",
            "no-ext",
            "hyphen",
            "extension-only",
            "dots-only",
        ],
    )
    def test_output_filename_is_zip_safe(
        self,
        stubBinary: Path,
        pdfBytes: bytes,
        behaviour: Any,
        filename: str,
        expected: str,
    ) -> None:
        behaviour("ok")
        result = convertPdfToHtml(
            pdfBytes, filename=filename, binaryPath=str(stubBinary)
        )
        assert result.filename == expected

    def test_non_zero_exit_raises(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        behaviour("fail")
        with pytest.raises(PdfConversionError):
            convertPdfToHtml(pdfBytes, filename="a.pdf", binaryPath=str(stubBinary))

    def test_non_zero_exit_reports_the_tool_stderr(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        """The user sees this message; it has to say something useful."""
        behaviour("fail")
        with pytest.raises(PdfConversionError, match="cannot read PDF"):
            convertPdfToHtml(pdfBytes, filename="a.pdf", binaryPath=str(stubBinary))

    def test_failure_names_the_backend(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any, backend: Any
    ) -> None:
        """With two converters in play, "it failed" is not enough to act on."""
        behaviour("fail")
        with pytest.raises(PdfConversionError, match=backend.name):
            convertPdfToHtml(pdfBytes, filename="a.pdf", binaryPath=str(stubBinary))

    def test_timeout_raises(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        """A pathological PDF must not hold a web request open indefinitely."""
        behaviour("hang")
        with pytest.raises(PdfConversionError, match="timed out"):
            convertPdfToHtml(
                pdfBytes,
                filename="a.pdf",
                binaryPath=str(stubBinary),
                timeoutSeconds=1,
            )

    def test_missing_output_file_raises(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        """Exit 0 is not proof of success."""
        behaviour("empty")
        with pytest.raises(PdfConversionError):
            convertPdfToHtml(pdfBytes, filename="a.pdf", binaryPath=str(stubBinary))

    def test_unparseable_output_raises(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        behaviour("garbage")
        with pytest.raises(PdfConversionError):
            convertPdfToHtml(pdfBytes, filename="a.pdf", binaryPath=str(stubBinary))

    def test_no_temporary_files_are_left_behind(
        self,
        stubBinary: Path,
        pdfBytes: bytes,
        behaviour: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Redirected to a temp root of our own, for both this process and the
        converter it spawns. Watching the process-wide temp directory instead
        makes this fail whenever anything else on the machine happens to write
        there during the call."""
        behaviour("ok")
        tempRoot = tmp_path / "temp-root"
        tempRoot.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(tempRoot))
        for var in ("TMPDIR", "TEMP", "TMP"):
            monkeypatch.setenv(var, str(tempRoot))

        convertPdfToHtml(pdfBytes, filename="a.pdf", binaryPath=str(stubBinary))
        assert list(tempRoot.iterdir()) == []

    def test_survives_a_failure_and_still_works_after(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        behaviour("fail")
        with pytest.raises(PdfConversionError):
            convertPdfToHtml(pdfBytes, filename="a.pdf", binaryPath=str(stubBinary))
        behaviour("ok")
        assert convertPdfToHtml(
            pdfBytes, filename="b.pdf", binaryPath=str(stubBinary)
        ).fileContent

    def test_a_resolved_converter_is_reused(
        self, stubBinary: Path, pdfBytes: bytes, behaviour: Any
    ) -> None:
        """Batch conversion resolves once and passes the result in."""
        behaviour("ok")
        converter = findConverter(str(stubBinary))
        for name in ("a.pdf", "b.pdf"):
            assert convertPdfToHtml(
                pdfBytes, filename=name, converter=converter
            ).fileContent


# ── argv construction, per backend ────────────────────────────────────────────


@pytest.fixture
def capturedArgv(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record argv without running anything; conversion then fails on no output."""
    calls: list[list[str]] = []

    def spy(args: Any, **kwargs: Any) -> Any:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", spy)
    return calls


def _runCapturing(binary: str) -> None:
    with pytest.raises(PdfConversionError):
        runConverter(b"%PDF-1.7", binaryPath=binary)


class TestArgvCommonToAllBackends:
    def test_binary_is_argv0(
        self, capturedArgv: list[list[str]], stubBinary: Path
    ) -> None:
        _runCapturing(str(stubBinary))
        assert Path(capturedArgv[0][0]) == stubBinary

    def test_every_argument_is_a_string(
        self, capturedArgv: list[list[str]], stubBinary: Path
    ) -> None:
        """A string argv would mean shell interpretation of user filenames."""
        _runCapturing(str(stubBinary))
        assert all(isinstance(arg, str) for arg in capturedArgv[0])

    def test_no_user_supplied_name_reaches_the_command_line(
        self, capturedArgv: list[list[str]], stubBinary: Path
    ) -> None:
        """The converter always writes a fixed name inside a private temp dir,
        so an uploaded filename is never an argument at all."""
        _runCapturing(str(stubBinary))
        assert not any("annex" in arg for arg in capturedArgv[0])

    def test_input_pdf_is_passed(
        self, capturedArgv: list[list[str]], stubBinary: Path
    ) -> None:
        _runCapturing(str(stubBinary))
        assert any(arg.endswith("input.pdf") for arg in capturedArgv[0])


class TestPdf2HtmlExArgv:
    @pytest.fixture(autouse=True)
    def run(self, capturedArgv: list[list[str]], stubBinaries: dict) -> list[str]:
        _runCapturing(str(stubBinaries["pdf2htmlEX"]))
        return capturedArgv[0]

    def test_embeds_everything_into_one_file(self, run: list[str]) -> None:
        """The output has to stand alone inside the report package."""
        for flag in (
            "--embed-css",
            "--embed-font",
            "--embed-image",
            "--embed-javascript",
        ):
            assert run[run.index(flag) + 1] == "1"

    def test_does_not_split_pages(self, run: list[str]) -> None:
        """Split pages would produce many files, not one document set member."""
        assert run[run.index("--split-pages") + 1] == "0"

    def test_writes_into_a_dest_dir(self, run: list[str]) -> None:
        assert "--dest-dir" in run

    def test_output_name_is_the_last_argument(self, run: list[str]) -> None:
        assert run[-1] == "output.html"

    def test_does_not_pass_quiet(self, run: list[str]) -> None:
        """pdf2htmlEX 0.14.8 has no --quiet and exits non-zero on an unknown
        option, which would fail every conversion rather than just be noisy."""
        assert "--quiet" not in run

    def test_every_flag_is_one_the_binary_accepts(self, run: list[str]) -> None:
        """Guards against another invented flag. This list was checked against
        pdf2htmlEX 0.14.8 --help; anything new must be too."""
        known = {
            "--embed-css",
            "--embed-font",
            "--embed-image",
            "--embed-javascript",
            "--embed-outline",
            "--svg-embed-bitmap",
            "--split-pages",
            "--process-outline",
            "--printing",
            "--dest-dir",
        }
        assert {a for a in run if a.startswith("--")} <= known


class TestPdfToHtmlArgv:
    @pytest.fixture(autouse=True)
    def run(self, capturedArgv: list[list[str]], stubBinaries: dict) -> list[str]:
        _runCapturing(str(stubBinaries["pdftohtml"]))
        return capturedArgv[0]

    def test_renders_pages_faithfully(self, run: list[str]) -> None:
        """-c renders each page as a bitmap rather than approximating layout."""
        assert "-c" in run

    def test_produces_one_document(self, run: list[str]) -> None:
        """Without these it emits a frameset and a file per page."""
        assert "-s" in run and "-noframes" in run

    def test_output_path_is_the_last_argument(self, run: list[str]) -> None:
        assert run[-1].endswith("output.html")

    def test_does_not_pass_dataurls(self, run: list[str]) -> None:
        """Poppler builds without it exit non-zero and print usage; the images
        are inlined afterwards instead."""
        assert "-dataurls" not in run


class TestSubprocessKeywords:
    def test_never_uses_a_shell_and_closes_stdin(
        self, monkeypatch: pytest.MonkeyPatch, stubBinary: Path
    ) -> None:
        seen: dict[str, Any] = {}

        def spy(args: Any, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", spy)
        with pytest.raises(PdfConversionError):
            runConverter(b"%PDF", binaryPath=str(stubBinary))

        assert seen["shell"] is False
        assert seen["stdin"] is subprocess.DEVNULL
        assert seen["encoding"] == "utf-8"
        assert seen["timeout"] == 60
        assert seen["check"] is True

    def test_timeout_is_forwarded(
        self, monkeypatch: pytest.MonkeyPatch, stubBinary: Path
    ) -> None:
        seen: dict[str, Any] = {}

        def spy(args: Any, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", spy)
        with pytest.raises(PdfConversionError):
            runConverter(b"%PDF", binaryPath=str(stubBinary), timeoutSeconds=5)
        assert seen["timeout"] == 5


class TestUnexpectedFailures:
    @pytest.mark.parametrize(
        "raised",
        [OSError("disk on fire"), subprocess.SubprocessError("something odd")],
        ids=["oserror", "subprocesserror"],
    )
    def test_are_wrapped_not_propagated(
        self, monkeypatch: pytest.MonkeyPatch, stubBinary: Path, raised: Exception
    ) -> None:
        """A failed PDF is a warning in the UI, so nothing may escape as itself."""

        def boom(*args: Any, **kwargs: Any) -> Any:
            raise raised

        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(PdfConversionError):
            runConverter(b"%PDF", binaryPath=str(stubBinary))
