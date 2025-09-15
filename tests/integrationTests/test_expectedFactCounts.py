import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Generator

import pytest
from lxml import etree


def is_main_or_pr_to_main() -> bool:
    github_ref = os.environ.get("GITHUB_REF", "")
    github_base_ref = os.environ.get("GITHUB_BASE_REF", "")

    return github_ref.endswith("/main") or github_base_ref == "main"


def force_run() -> bool:
    value = os.environ.get("FORCE_RUN", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


skip_if_not_main_unless_forced = pytest.mark.skipif(
    not (force_run() or is_main_or_pr_to_main()),
    reason="Validation tests are slow and only run on main branch or PRs targeting main",
)

IX_NAMESPACE = "http://www.xbrl.org/2013/inlineXBRL"

TEST_CASES = [
    ("tests/data/VSME-Digital-Template-Sample-1.0.0.xlsx", 142),
    ("tests/data/VSME-Digital-Template-Sample-1.0.1.xlsx", 142),
    ("tests/data/vsme-unit-test-v1.0.0.xlsx", 159),
]


def format_subprocess_output(result: subprocess.CompletedProcess) -> str:
    return f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"


@pytest.fixture(scope="module")
def parsed_reports(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[dict, None, None]:
    """Run parse-and-ixbrl.py on all test cases once, yield dict of input_file -> output_file."""
    outputs = {}
    base_tmp = tmp_path_factory.mktemp("parsed_reports")

    for input_file, _ in TEST_CASES:
        input_path = Path(input_file)
        output_file = base_tmp / (Path(input_file).stem + ".html")

        result = subprocess.run(
            [
                sys.executable,
                "scripts/parse-and-ixbrl.py",
                "--skip-validation",
                str(input_path),
                str(output_file),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            f"Script failed for {input_file}:\n{format_subprocess_output(result)}"
        )
        assert output_file.exists(), (
            f"Output HTML file was not created for {input_file}.\n"
            f"{format_subprocess_output(result)}"
        )

        outputs[input_file] = output_file

    yield outputs


@pytest.mark.parametrize("input_file,expected_fact_count", TEST_CASES)
def test_fact_count(
    parsed_reports: dict[str, Path], input_file: str, expected_fact_count: int
) -> None:
    output_file = parsed_reports[input_file]

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


@skip_if_not_main_unless_forced
@pytest.mark.parametrize("input_file,_", TEST_CASES)
def test_validation(parsed_reports: dict[str, Path], input_file: str, _: int) -> None:
    output_file = parsed_reports[input_file]

    validate_result = subprocess.run(
        [
            sys.executable,
            "scripts/check-report.py",
            "--ignore-calculation-warnings",
            str(output_file),
        ],
        capture_output=True,
        text=True,
    )

    assert validate_result.returncode == 0, (
        f"Validation failed for {output_file}:\n{format_subprocess_output(validate_result)}"
    )
