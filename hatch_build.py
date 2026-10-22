"""Build hook: compiles the report string catalogs with
scripts/compile-report-translations.py, and puts them in the wheel beside the catalogs."""

import importlib.util
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

SCRIPT = Path(__file__).resolve().parent / "scripts" / "compile-report-translations.py"


class CompileReportTranslations(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        spec = importlib.util.spec_from_file_location("compile_translations", SCRIPT)
        assert spec is not None and spec.loader is not None
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        script.compileCatalogs()
        if self.target_name == "wheel" and version != "editable":
            build_data["force_include"][str(script.COMPILED)] = (
                "mireport/data/disclosures/translations"
            )
