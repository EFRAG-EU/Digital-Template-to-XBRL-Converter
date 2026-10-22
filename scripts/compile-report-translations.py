"""Compile the report string catalogs to the .mo files the report reads.

Each ``<language>.po`` under ``src/mireport/data/disclosures/translations/`` becomes a
``<language>.mo`` at the same place under ``generated/translations/``, which is not checked in.
Building the package runs this (``hatch_build.py``), editable installs included, and puts the
.mo files in the wheel; run it by hand after changing a catalog in a checkout.
"""

import shutil
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parents[1]
CATALOGS = ROOT / "src/mireport/data/disclosures/translations"
COMPILED = ROOT / "generated/translations"


def compileCatalogs(source: Path = CATALOGS, target: Path = COMPILED) -> list[Path]:
    """Each catalog under ``source`` compiled to ``target``, fuzzy entries left out. What
    was in ``target`` before is removed, so a deleted catalog leaves no .mo behind."""
    shutil.rmtree(target, ignore_errors=True)
    written = []
    for po in sorted(source.rglob("*.po")):
        mo = target / po.relative_to(source).with_suffix(".mo")
        mo.parent.mkdir(parents=True, exist_ok=True)
        with po.open("rb") as f:
            catalog = read_po(f)
        with mo.open("wb") as f:
            write_mo(f, catalog)
        written.append(mo)
    return written


def main() -> None:
    for mo in compileCatalogs():
        print(mo.relative_to(ROOT))


if __name__ == "__main__":
    main()
