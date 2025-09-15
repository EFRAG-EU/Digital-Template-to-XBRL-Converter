import logging
from importlib.metadata import PackageNotFoundError, version

from mireport.data import taxonomies
from mireport.json import getJsonFiles, getObject
from mireport.taxonomy import _loadTaxonomyFromFile

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["loadTaxonomyJSON"]

try:
    __version__ = version("mireport")
except PackageNotFoundError:
    __version__ = "(unknown version)"


def loadTaxonomyJSON() -> None:
    """Loads the taxonomies, unit registry and other models."""
    for f in getJsonFiles(taxonomies):
        try:
            _loadTaxonomyFromFile(getObject(f))
        except Exception as e:
            logging.error(f"Error loading taxonomy from {f.name}", exc_info=e)
