from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mireport.exceptions import TaxonomyPackageException
from mireport.taxonomy_package import entryPointsFromPackage

TP_2016 = "http://xbrl.org/2016/taxonomy-package"
TP_PWD_2014 = "http://xbrl.org/PWD/2014-01-15/taxonomy-package"

VSME_METADATA = f"""<?xml version="1.0" encoding="UTF-8"?>
<tp:taxonomyPackage xmlns:tp="{TP_2016}" xml:lang="en">
  <tp:identifier>https://xbrl.efrag.org/taxonomy/vsme/2026-05-01/2026-02-v1.2.0</tp:identifier>
  <tp:name>VSME XBRL Taxonomy February 2026</tp:name>
  <tp:entryPoints>
    <tp:entryPoint>
      <tp:name>VSME All reporting entry point</tp:name>
      <tp:description>To be used for reporting.</tp:description>
      <tp:entryPointDocument href="https://xbrl.efrag.org/taxonomy/vsme/2026-05-01/vsme-all.xsd"/>
    </tp:entryPoint>
  </tp:entryPoints>
</tp:taxonomyPackage>
"""

ESRS_METADATA = f"""<?xml version="1.0" encoding="UTF-8"?>
<tp:taxonomyPackage xmlns:tp="{TP_2016}" xml:lang="en">
  <tp:name>ESRS XBRL Taxonomy 2024 (Set 1)</tp:name>
  <tp:entryPoints>
    <tp:entryPoint>
      <tp:name>ESRS All</tp:name>
      <tp:entryPointDocument href="https://xbrl.efrag.org/taxonomy/esrs/2023-12-22/esrs_all.xsd"/>
    </tp:entryPoint>
    <tp:entryPoint>
      <tp:name>ESRS Core</tp:name>
      <tp:entryPointDocument href="https://xbrl.efrag.org/taxonomy/esrs/2023-12-22/common/esrs_cor.xsd"/>
    </tp:entryPoint>
  </tp:entryPoints>
</tp:taxonomyPackage>
"""


def makePackage(
    tmp_path: Path,
    metadata: str | None,
    *,
    name: str = "taxonomy.zip",
    member: str = "taxonomy/META-INF/taxonomyPackage.xml",
) -> Path:
    zipPath = tmp_path / name
    with zipfile.ZipFile(zipPath, "w") as zf:
        zf.writestr("taxonomy/some.xsd", "<xsd/>")
        if metadata is not None:
            zf.writestr(member, metadata)
    return zipPath


class TestEntryPointsFromPackage:
    def test_single_entry_point(self, tmp_path):
        zipPath = makePackage(tmp_path, VSME_METADATA)
        (entryPoint,) = entryPointsFromPackage(zipPath)
        assert entryPoint.name == "VSME All reporting entry point"
        assert entryPoint.description == "To be used for reporting."
        assert entryPoint.hrefs == (
            "https://xbrl.efrag.org/taxonomy/vsme/2026-05-01/vsme-all.xsd",
        )
        assert entryPoint.packageName == "VSME XBRL Taxonomy February 2026"
        assert entryPoint.packagePath == zipPath

    def test_multiple_entry_points_without_descriptions(self, tmp_path):
        entryPoints = entryPointsFromPackage(makePackage(tmp_path, ESRS_METADATA))
        assert [ep.name for ep in entryPoints] == ["ESRS All", "ESRS Core"]
        assert [ep.description for ep in entryPoints] == [None, None]

    def test_root_level_meta_inf(self, tmp_path):
        zipPath = makePackage(
            tmp_path, VSME_METADATA, member="META-INF/taxonomyPackage.xml"
        )
        assert len(entryPointsFromPackage(zipPath)) == 1

    def test_older_namespace_accepted(self, tmp_path):
        metadata = VSME_METADATA.replace(TP_2016, TP_PWD_2014)
        assert len(entryPointsFromPackage(makePackage(tmp_path, metadata))) == 1

    def test_unknown_namespace_rejected(self, tmp_path):
        metadata = VSME_METADATA.replace(TP_2016, "http://example.com/not-a-package")
        with pytest.raises(TaxonomyPackageException, match="unrecognised namespace"):
            entryPointsFromPackage(makePackage(tmp_path, metadata))

    def test_xml_base_resolved(self, tmp_path):
        metadata = f"""<tp:taxonomyPackage xmlns:tp="{TP_2016}"
            xmlns:xml="http://www.w3.org/XML/1998/namespace">
          <tp:entryPoints><tp:entryPoint>
            <tp:name>Relative</tp:name>
            <tp:entryPointDocument
                xml:base="https://example.com/taxonomy/2026/"
                href="core.xsd"/>
          </tp:entryPoint></tp:entryPoints>
        </tp:taxonomyPackage>"""
        (entryPoint,) = entryPointsFromPackage(makePackage(tmp_path, metadata))
        assert entryPoint.hrefs == ("https://example.com/taxonomy/2026/core.xsd",)

    def test_english_name_preferred(self, tmp_path):
        metadata = f"""<tp:taxonomyPackage xmlns:tp="{TP_2016}"
            xmlns:xml="http://www.w3.org/XML/1998/namespace" xml:lang="fr">
          <tp:entryPoints><tp:entryPoint>
            <tp:name xml:lang="fr">Point d'entree</tp:name>
            <tp:name xml:lang="en-GB">Entry point</tp:name>
            <tp:entryPointDocument href="https://example.com/a.xsd"/>
          </tp:entryPoint></tp:entryPoints>
        </tp:taxonomyPackage>"""
        (entryPoint,) = entryPointsFromPackage(makePackage(tmp_path, metadata))
        assert entryPoint.name == "Entry point"

    def test_first_name_used_when_no_english(self, tmp_path):
        metadata = f"""<tp:taxonomyPackage xmlns:tp="{TP_2016}"
            xmlns:xml="http://www.w3.org/XML/1998/namespace">
          <tp:entryPoints><tp:entryPoint>
            <tp:name xml:lang="fr">Point d'entree</tp:name>
            <tp:name xml:lang="de">Einstiegspunkt</tp:name>
            <tp:entryPointDocument href="https://example.com/a.xsd"/>
          </tp:entryPoint></tp:entryPoints>
        </tp:taxonomyPackage>"""
        (entryPoint,) = entryPointsFromPackage(makePackage(tmp_path, metadata))
        assert entryPoint.name == "Point d'entree"

    def test_unnamed_entry_point(self, tmp_path):
        metadata = f"""<tp:taxonomyPackage xmlns:tp="{TP_2016}">
          <tp:entryPoints><tp:entryPoint>
            <tp:entryPointDocument href="https://example.com/a.xsd"/>
          </tp:entryPoint></tp:entryPoints>
        </tp:taxonomyPackage>"""
        (entryPoint,) = entryPointsFromPackage(makePackage(tmp_path, metadata))
        assert entryPoint.name == "<unnamed 1>"

    def test_entry_point_with_two_documents(self, tmp_path):
        metadata = f"""<tp:taxonomyPackage xmlns:tp="{TP_2016}">
          <tp:entryPoints><tp:entryPoint>
            <tp:name>Combined</tp:name>
            <tp:entryPointDocument href="https://example.com/a.xsd"/>
            <tp:entryPointDocument href="https://example.com/b.xsd"/>
          </tp:entryPoint></tp:entryPoints>
        </tp:taxonomyPackage>"""
        # Both documents belong to one entry point, so this stays a single result.
        (entryPoint,) = entryPointsFromPackage(makePackage(tmp_path, metadata))
        assert entryPoint.name == "Combined"
        assert entryPoint.hrefs == (
            "https://example.com/a.xsd",
            "https://example.com/b.xsd",
        )

    def test_entry_point_without_documents_skipped(self, tmp_path):
        metadata = f"""<tp:taxonomyPackage xmlns:tp="{TP_2016}">
          <tp:entryPoints>
            <tp:entryPoint><tp:name>Nothing to load</tp:name></tp:entryPoint>
            <tp:entryPoint>
              <tp:name>Real</tp:name>
              <tp:entryPointDocument href="https://example.com/a.xsd"/>
            </tp:entryPoint>
          </tp:entryPoints>
        </tp:taxonomyPackage>"""
        (entryPoint,) = entryPointsFromPackage(makePackage(tmp_path, metadata))
        assert entryPoint.name == "Real"

    def test_no_metadata_file(self, tmp_path):
        with pytest.raises(TaxonomyPackageException, match="No META-INF"):
            entryPointsFromPackage(makePackage(tmp_path, None))

    def test_not_a_zip(self, tmp_path):
        notAZip = tmp_path / "notazip.zip"
        notAZip.write_bytes(b"this is not a zip file")
        with pytest.raises(TaxonomyPackageException, match="Could not read"):
            entryPointsFromPackage(notAZip)

    def test_missing_file(self, tmp_path):
        with pytest.raises(TaxonomyPackageException, match="Could not read"):
            entryPointsFromPackage(tmp_path / "nope.zip")

    def test_malformed_xml(self, tmp_path):
        with pytest.raises(TaxonomyPackageException, match="Could not parse"):
            entryPointsFromPackage(makePackage(tmp_path, "<tp:taxonomyPackage>"))

    def test_accepts_str_path(self, tmp_path):
        zipPath = makePackage(tmp_path, VSME_METADATA)
        (entryPoint,) = entryPointsFromPackage(str(zipPath))
        assert entryPoint.packagePath == zipPath
