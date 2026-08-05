"""Read the entry points declared by a taxonomy package zip.

Deliberately stdlib-only: this lets the CLI offer a list of entry points to pick
from without paying for an Arelle start-up.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree

from mireport.exceptions import TaxonomyPackageException

METADATA_FILENAME = "META-INF/taxonomyPackage.xml"

# Every namespace Arelle recognises as taxonomy package metadata. See
# txmyPkgNSes in arelle/packages/_package_manager.py.
TAXONOMY_PACKAGE_NAMESPACES = frozenset(
    {
        "http://www.corefiling.com/xbrl/taxonomypackage/v1",
        "http://xbrl.org/PWD/2014-01-15/taxonomy-package",
        "http://xbrl.org/PWD/2015-01-14/taxonomy-package",
        "http://xbrl.org/PR/2015-12-09/taxonomy-package",
        "http://xbrl.org/2016/taxonomy-package",
        "http://xbrl.org/WGWD/YYYY-MM-DD/taxonomy-package",
    }
)

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"


@dataclass(frozen=True, slots=True)
class PackageEntryPoint:
    """One entry point declared by a taxonomy package.

    An entry point may name several documents, which together form a single DTS.
    """

    name: str
    description: str | None
    hrefs: tuple[str, ...]
    packagePath: Path
    packageName: str | None


def _findMetadataMember(zf: zipfile.ZipFile, zipPath: Path) -> str:
    names = zf.namelist()
    # A conforming package holds its metadata inside a single top level folder.
    for name in names:
        if name.endswith(f"/{METADATA_FILENAME}"):
            return name
    if METADATA_FILENAME in names:
        return METADATA_FILENAME
    raise TaxonomyPackageException(
        f"No {METADATA_FILENAME} found in taxonomy package {zipPath}."
    )


def _namespaceOf(element: ElementTree.Element) -> str:
    return element.tag.partition("}")[0][1:] if element.tag.startswith("{") else ""


def _textOf(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


def _langOf(element: ElementTree.Element, inherited: str | None) -> str | None:
    return element.get(XML_LANG, inherited)


def _bestForLanguage(candidates: list[tuple[str | None, str]]) -> str | None:
    """Prefer an English value, else the first one given."""
    for lang, text in candidates:
        if lang is not None and lang.lower().startswith("en"):
            return text
    return candidates[0][1] if candidates else None


def entryPointsFromPackage(zipPath: str | Path) -> list[PackageEntryPoint]:
    """Return every entry point declared by the taxonomy package at *zipPath*."""
    path = Path(zipPath)
    try:
        with zipfile.ZipFile(path) as zf:
            member = _findMetadataMember(zf, path)
            metadata = zf.read(member)
    except TaxonomyPackageException:
        raise
    except (OSError, zipfile.BadZipFile) as e:
        raise TaxonomyPackageException(f"Could not read taxonomy package {path}: {e}")

    try:
        root = ElementTree.fromstring(metadata)
    except ElementTree.ParseError as e:
        raise TaxonomyPackageException(f"Could not parse {member} in {path}: {e}")

    ns = _namespaceOf(root)
    if ns not in TAXONOMY_PACKAGE_NAMESPACES:
        raise TaxonomyPackageException(
            f"{member} in {path} uses unrecognised namespace {ns!r}."
        )

    tp = f"{{{ns}}}"
    rootLang = root.get(XML_LANG)
    packageName = _textOf(root.find(f"{tp}name"))

    entryPoints: list[PackageEntryPoint] = []
    for unnamedCount, entryPoint in enumerate(root.iter(f"{tp}entryPoint"), start=1):
        names = [
            (_langOf(n, rootLang), text)
            for n in entryPoint.iterfind(f"{tp}name")
            if (text := _textOf(n)) is not None
        ]
        name = _bestForLanguage(names) or f"<unnamed {unnamedCount}>"
        descriptions = [
            (_langOf(d, rootLang), text)
            for d in entryPoint.iterfind(f"{tp}description")
            if (text := _textOf(d)) is not None
        ]
        description = _bestForLanguage(descriptions)

        hrefs: list[str] = []
        for document in entryPoint.iterfind(f"{tp}entryPointDocument"):
            href = document.get("href")
            if not href:
                continue
            if (base := document.get(XML_BASE)) is not None:
                href = urljoin(base, href)
            hrefs.append(href)

        if not hrefs:
            continue
        entryPoints.append(
            PackageEntryPoint(
                name=name,
                description=description,
                hrefs=tuple(hrefs),
                packagePath=path,
                packageName=packageName,
            )
        )
    return entryPoints
