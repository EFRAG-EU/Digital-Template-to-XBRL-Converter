"""scripts/xbrljson-to-ixbrl.py with a report on a built-in taxonomy: the built-in
taxonomies are always loaded, so no --taxonomy-json is needed for it."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "xbrljson-to-ixbrl.py"

VSME = "https://xbrl.efrag.org/taxonomy/vsme/2026-05-01"
ENTITY = "lei:529900T8BM49AURSDO55"
DURATION = "2026-01-01T00:00:00/2027-01-01T00:00:00"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("xbrljson_to_ixbrl_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vsmeReport() -> dict[str, Any]:
    return {
        "documentInfo": {
            "documentType": "https://xbrl.org/2021/xbrl-json",
            "namespaces": {
                "lei": "http://standards.iso.org/iso/17442",
                "utr": "http://www.xbrl.org/2009/utr",
                "vsme": f"{VSME}/vsme",
                "xbrl": "https://xbrl.org/2021",
                "xbrli": "http://www.xbrl.org/2003/instance",
            },
            "taxonomy": [f"{VSME}/vsme-all.xsd"],
        },
        "facts": {
            "f1": {
                "value": "1250.5",
                "decimals": 1,
                "dimensions": {
                    "concept": "vsme:TotalEnergyConsumption",
                    "entity": ENTITY,
                    "period": DURATION,
                    "unit": "utr:MWh",
                },
            },
            "f2": {
                "value": "310",
                "decimals": 0,
                "dimensions": {
                    "concept": "vsme:GrossScope1GreenhouseGasEmissions",
                    "entity": ENTITY,
                    "period": DURATION,
                    "unit": "utr:tCO2e",
                },
            },
        },
    }


def test_taxonomy_json_is_optional(script: ModuleType) -> None:
    args = script.createArgParser().parse_args(["report.json", "out"])
    assert args.taxonomy_json == []


def test_report_on_a_built_in_taxonomy_converts(
    script: ModuleType, tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(vsmeReport()), encoding="utf-8")
    output = tmp_path / "out"
    args = script.createArgParser().parse_args(
        [str(report), str(output), "--strict", "--skip-validation"]
    )
    results = script.doConversion(args)
    assert not results.hasErrors()
    assert list(output.glob("*.html"))
