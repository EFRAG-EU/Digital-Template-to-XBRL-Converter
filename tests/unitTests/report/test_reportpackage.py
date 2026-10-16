"""Tests for report package zip assembly.

The layout assertions here are not cosmetic. Arelle decides what to load out of
a report package purely from the zip's directory structure:

- every file directly under ``{top}/reports/`` with a report extension becomes
  its own entry point (``arelle.packages.report.ReportPackage.getAllReportEntries``);
- files in a *sub*directory of ``reports/`` collapse into a single multi-file
  entry — an inline document set;
- but subdirectory entries are discarded outright whenever a top-level report
  also exists.

So "the report moves into the docset directory once there are members" is a
correctness requirement, not a preference.
"""

import zipfile
from io import BytesIO

import pytest

from mireport.filesupport import FilelikeAndFileName
from mireport.report.reportpackage import (
    UNCONSTRAINED_REPORT_PACKAGE_JSON,
    buildReportPackage,
)

TOP = "Acme_2024"


def makeFile(filename: str, content: bytes = b"<html/>") -> FilelikeAndFileName:
    return FilelikeAndFileName(fileContent=content, filename=filename)


REPORT = makeFile("Acme_2024_XBRL_Report.html", b"<html>tagged</html>")


def namelist(package: FilelikeAndFileName) -> list[str]:
    with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
        return zf.namelist()


def readEntry(package: FilelikeAndFileName, path: str) -> bytes:
    with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
        return zf.read(path)


class TestPlainPackage:
    def test_layout(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{TOP}/reports/Acme_2024_XBRL_Report.html",
        ]

    def test_package_json_is_unconstrained(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        assert (
            readEntry(package, f"{TOP}/META-INF/reportPackage.json")
            == UNCONSTRAINED_REPORT_PACKAGE_JSON
        )

    def test_report_content_survives(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        assert (
            readEntry(package, f"{TOP}/reports/Acme_2024_XBRL_Report.html")
            == REPORT.fileContent
        )

    def test_package_filename(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        assert package.filename == f"{TOP}_XBRL_Report.zip"

    def test_empty_sequences_are_the_same_as_omitting_them(self) -> None:
        assert namelist(
            buildReportPackage(REPORT, topLevel=TOP, docsetMembers=[], attachments=[])
        ) == namelist(buildReportPackage(REPORT, topLevel=TOP))

    def test_is_a_readable_deflated_zip(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            assert zf.testzip() is None
            assert all(
                info.compress_type == zipfile.ZIP_DEFLATED for info in zf.infolist()
            )


class TestDocumentSet:
    def test_report_moves_into_the_docset_directory(self) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, docsetMembers=[makeFile("annex1.html")]
        )
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{TOP}/reports/Acme_2024_XBRL_Report/Acme_2024_XBRL_Report.html",
            f"{TOP}/reports/Acme_2024_XBRL_Report/annex1.html",
        ]

    def test_nothing_is_left_directly_in_reports(self) -> None:
        """A top-level report would make Arelle discard the whole docset."""
        package = buildReportPackage(
            REPORT, topLevel=TOP, docsetMembers=[makeFile("annex1.html")]
        )
        directlyInReports = [
            name
            for name in namelist(package)
            if name.startswith(f"{TOP}/reports/")
            and "/" not in name[len(f"{TOP}/reports/") :]
        ]
        assert directlyInReports == []

    def test_report_is_written_first(self) -> None:
        """Docset order is zip write order, and the tagged report must lead."""
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex1.html"), makeFile("annex2.html")],
        )
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            inDocset = [
                info.filename
                for info in zf.infolist()
                if info.filename.startswith(f"{TOP}/reports/Acme_2024_XBRL_Report/")
            ]
        assert inDocset[0].endswith("/Acme_2024_XBRL_Report.html")

    def test_member_order_is_preserved(self) -> None:
        members = [makeFile(f"annex{i}.html") for i in (3, 1, 2)]
        package = buildReportPackage(REPORT, topLevel=TOP, docsetMembers=members)
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            names = [info.filename.rsplit("/", 1)[-1] for info in zf.infolist()]
        assert names[-3:] == ["annex3.html", "annex1.html", "annex2.html"]

    def test_member_content_survives(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex1.html", b"<html>from pdf</html>")],
        )
        assert (
            readEntry(package, f"{TOP}/reports/Acme_2024_XBRL_Report/annex1.html")
            == b"<html>from pdf</html>"
        )

    def test_docset_directory_is_named_for_the_report_stem(self) -> None:
        report = makeFile("Report.With.Dots.html")
        package = buildReportPackage(
            report, topLevel=TOP, docsetMembers=[makeFile("annex1.html")]
        )
        assert f"{TOP}/reports/Report.With.Dots/Report.With.Dots.html" in namelist(
            package
        )


class TestAttachments:
    def test_attachments_live_outside_reports(self) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, attachments=[makeFile("annex1.pdf", b"%PDF-1.7")]
        )
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{TOP}/reports/Acme_2024_XBRL_Report.html",
            f"{TOP}/attachments/annex1.pdf",
        ]

    def test_attachments_alone_do_not_move_the_report(self) -> None:
        """Only docset members trigger the subdirectory layout."""
        package = buildReportPackage(
            REPORT, topLevel=TOP, attachments=[makeFile("annex1.pdf", b"%PDF-1.7")]
        )
        assert f"{TOP}/reports/Acme_2024_XBRL_Report.html" in namelist(package)

    def test_attachment_content_survives(self) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, attachments=[makeFile("annex1.pdf", b"%PDF-1.7 body")]
        )
        assert readEntry(package, f"{TOP}/attachments/annex1.pdf") == b"%PDF-1.7 body"

    def test_docset_and_attachments_together(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex1.html")],
            attachments=[makeFile("annex1.pdf", b"%PDF-1.7")],
        )
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{TOP}/reports/Acme_2024_XBRL_Report/Acme_2024_XBRL_Report.html",
            f"{TOP}/reports/Acme_2024_XBRL_Report/annex1.html",
            f"{TOP}/attachments/annex1.pdf",
        ]


class TestCollisions:
    def test_duplicate_members_are_suffixed(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[
                makeFile("annex.html", b"one"),
                makeFile("annex.html", b"two"),
            ],
        )
        docset = f"{TOP}/reports/Acme_2024_XBRL_Report"
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{docset}/Acme_2024_XBRL_Report.html",
            f"{docset}/annex.html",
            f"{docset}/annex_1.html",
        ]
        assert readEntry(package, f"{docset}/annex.html") == b"one"
        assert readEntry(package, f"{docset}/annex_1.html") == b"two"

    def test_member_colliding_with_the_report_is_suffixed(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("Acme_2024_XBRL_Report.html", b"from pdf")],
        )
        docset = f"{TOP}/reports/Acme_2024_XBRL_Report"
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{docset}/Acme_2024_XBRL_Report.html",
            f"{docset}/Acme_2024_XBRL_Report_1.html",
        ]
        assert (
            readEntry(package, f"{docset}/Acme_2024_XBRL_Report.html")
            == REPORT.fileContent
        )

    def test_duplicate_attachments_are_suffixed(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            attachments=[makeFile("a.pdf", b"one"), makeFile("a.pdf", b"two")],
        )
        assert f"{TOP}/attachments/a.pdf" in namelist(package)
        assert f"{TOP}/attachments/a_1.pdf" in namelist(package)

    def test_collisions_are_counted_per_directory(self) -> None:
        """An attachment never collides with a docset member — different dirs."""
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex.html")],
            attachments=[makeFile("annex.html")],
        )
        assert f"{TOP}/reports/Acme_2024_XBRL_Report/annex.html" in namelist(package)
        assert f"{TOP}/attachments/annex.html" in namelist(package)

    def test_three_way_collision(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("a.html", bytes([i])) for i in range(3)],
        )
        docset = f"{TOP}/reports/Acme_2024_XBRL_Report"
        assert namelist(package)[1:] == [
            f"{docset}/Acme_2024_XBRL_Report.html",
            f"{docset}/a.html",
            f"{docset}/a_1.html",
            f"{docset}/a_2.html",
        ]

    def test_a_collision_never_raises(self) -> None:
        """PDFs are additive; a name clash must not sink a valid conversion."""
        buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("a.html")] * 5,
            attachments=[makeFile("a.pdf")] * 5,
        )


class TestZipSafety:
    @pytest.mark.parametrize(
        "filename",
        [
            "../escape.html",
            "sub/dir.html",
            "back\\slash.html",
            "  spaces  .html",
            "weiřd–dash.html",
        ],
        ids=["dotdot", "slash", "backslash", "spaces", "non-ascii"],
    )
    def test_hostile_member_names_are_sanitised(self, filename: str) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, docsetMembers=[makeFile(filename)]
        )
        docset = f"{TOP}/reports/Acme_2024_XBRL_Report/"
        members = [n for n in namelist(package) if n.startswith(docset)]
        assert len(members) == 2
        for name in members:
            tail = name[len(docset) :]
            assert "/" not in tail
            assert "\\" not in tail
            assert ".." not in tail.split(".")

    @pytest.mark.parametrize(
        "filename",
        ["../escape.pdf", "sub/dir.pdf", "back\\slash.pdf"],
        ids=["dotdot", "slash", "backslash"],
    )
    def test_hostile_attachment_names_are_sanitised(self, filename: str) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, attachments=[makeFile(filename)]
        )
        prefix = f"{TOP}/attachments/"
        attachments = [n for n in namelist(package) if n.startswith(prefix)]
        assert len(attachments) == 1
        tail = attachments[0][len(prefix) :]
        assert "/" not in tail and "\\" not in tail and ".." not in tail.split(".")

    def test_no_entry_uses_a_backslash_separator(self) -> None:
        """Arelle rejects the whole package on a single backslash separator."""
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("a\\b.html")],
            attachments=[makeFile("c\\d.pdf")],
        )
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            assert all("\\" not in info.orig_filename for info in zf.infolist())

    def test_exactly_one_top_level_directory(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex1.html")],
            attachments=[makeFile("annex1.pdf")],
        )
        assert {name.split("/")[0] for name in namelist(package)} == {TOP}

    def test_no_duplicate_entries(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("a.html")] * 3,
            attachments=[makeFile("a.pdf")] * 3,
        )
        names = namelist(package)
        assert len(names) == len(set(names))
