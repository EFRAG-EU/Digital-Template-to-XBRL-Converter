import logging

from mireport.data import taxonomies
from mireport.json import getJsonFiles, getObject
from mireport.taxonomy import _loadTaxonomyFromFile
from mireport.version import OUR_VERSION

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["loadTaxonomyJSON"]
__version__ = OUR_VERSION


def loadTaxonomyJSON() -> None:
    """Loads the taxonomies, unit registry and other models."""
    for f in getJsonFiles(taxonomies):
        try:
            _loadTaxonomyFromFile(getObject(f))
        except Exception as e:
            logging.error(f"Error loading taxonomy from {f.name}", exc_info=e)
