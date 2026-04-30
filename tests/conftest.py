import os

import pytest


def _is_main_or_pr_to_main() -> bool:
    github_ref = os.environ.get("GITHUB_REF", "")
    github_base_ref = os.environ.get("GITHUB_BASE_REF", "")
    return github_ref.endswith("/main") or github_base_ref == "main"


def _force_run() -> bool:
    value = os.environ.get("FORCE_RUN", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow tests (marked with @pytest.mark.slow)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--run-slow") or _force_run() or _is_main_or_pr_to_main():
        return
    skip = pytest.mark.skip(
        reason="Slow test — pass --run-slow to run (or set FORCE_RUN=1)"
    )
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
