import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from lxml import etree
from tests.offline_arelle import taxonomyPackagesDir

IX_NAMESPACE = "http://www.xbrl.org/2013/inlineXBRL"

# (workbook, expected fact count, the set of taxonomy packages to validate with)
TEST_CASES = [
    ("tests/data/vsme-unit-test-v1.0.0.xlsx", 159, "vsme-2024-12-17"),
    ("tests/data/VSME-Digital-Template-Sample-1.0.0.xlsx", 142, "vsme-2024-12-17"),
    ("tests/data/VSME-Digital-Template-Sample-1.0.1.xlsx", 142, "vsme-2024-12-17"),
    ("tests/data/VSME-Digital-Template-Sample-1.1.0.xlsx", 142, "default"),
    ("tests/data/VSME-Digital-Template-Sample-1.1.1.xlsx", 141, "default"),
    ("tests/data/VSME-Digital-Template-Sample-1.2.0.xlsx", 145, "default"),
]


def format_subprocess_output(result: subprocess.CompletedProcess) -> str:
    return f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"


@pytest.fixture(
    scope="module",
    params=[
        # One xdist group per workbook, so its conversion runs on one worker
        # (see test_docset_package.py) while the workbooks spread across them.
        pytest.param(case, id=case[0], marks=pytest.mark.xdist_group(case[0]))
        for case in TEST_CASES
    ],
)
def parsed_report(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[str, Path, int, str]:
    """Run parse-and-ixbrl.py on one test case; yield (input, output, expected count,
    package set).

    One fixture instance per input file lets the six conversions run on
    different workers instead of back to back in one setup.
    """
    input_file, expected_fact_count, packageSet = request.param
    output_file = (
        tmp_path_factory.mktemp("parsed_reports") / f"{Path(input_file).stem}.html"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/parse-and-ixbrl.py",
            "--skip-validation",
            input_file,
            str(output_file),
        ],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, (
        f"Script failed for {input_file}:\n{format_subprocess_output(result)}"
    )
    assert output_file.exists(), (
        f"Output HTML file was not created for {input_file}.\n"
        f"{format_subprocess_output(result)}"
    )
    return input_file, output_file, expected_fact_count, packageSet


def test_fact_count(parsed_report: tuple[str, Path, int, str]) -> None:
    input_file, output_file, expected_fact_count, _ = parsed_report

    parser = etree.XMLParser()
    tree = etree.parse(str(output_file), parser)
    root = tree.getroot()

    ns = {"ix": IX_NAMESPACE}
    fact_elements: Any = root.xpath("//ix:nonNumeric | //ix:nonFraction", namespaces=ns)
    if not isinstance(fact_elements, list):
        fact_elements = list(fact_elements) if fact_elements is not None else []
    actual_fact_count = len(fact_elements)

    assert actual_fact_count == expected_fact_count, (
        f"For {input_file}, expected {expected_fact_count} facts but found {actual_fact_count}"
    )


@pytest.mark.slow
def test_validation(parsed_report: tuple[str, Path, int, str]) -> None:
    _, output_file, _, packageSet = parsed_report

    validate_result = subprocess.run(
        [
            sys.executable,
            "scripts/check-report.py",
            "--ignore-calculation-warnings",
            str(output_file),
            "--taxonomy-packages",
            str(taxonomyPackagesDir(packageSet)),
        ],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )

    assert validate_result.returncode == 0, (
        f"Validation failed for {output_file}:\n{format_subprocess_output(validate_result)}"
    )
