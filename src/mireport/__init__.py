import logging

from mireport.data import taxonomies
from mireport.json import getJsonFiles, getObject
from mireport.taxonomy import loadBuiltInTaxonomyJSON
from mireport.version import OUR_VERSION

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["loadBuiltInTaxonomyJSON"]
__version__ = OUR_VERSION

