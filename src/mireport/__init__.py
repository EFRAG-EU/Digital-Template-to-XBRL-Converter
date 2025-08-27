import logging
from importlib.metadata import PackageNotFoundError, version

from mireport.data import excel_templates, taxonomies
from mireport.excelprocessor import _loadVsmeDefaults
from mireport.json import getJsonFiles, getObject, getResource
from mireport.taxonomy import _loadTaxonomyFromFile

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["loadMetaData"]

try:
    __version__ = version("mireport")
except PackageNotFoundError:
    __version__ = "(unknown version)"


def loadMetaData() -> None:
    """Loads the taxonomies, unit registry and other models."""
    for f in getJsonFiles(taxonomies):
        try:
            _loadTaxonomyFromFile(getObject(f))
        except Exception as e:
            logging.error(f"Error loading taxonomy from {f.name}", exc_info=e)

    _loadVsmeDefaults(getObject(getResource(excel_templates, "vsme.json")))
