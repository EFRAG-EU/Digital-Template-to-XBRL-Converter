import logging

from .taxonomy import loadTaxonomyJSON
from .version import OUR_VERSION

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["loadTaxonomyJSON"]
__version__ = OUR_VERSION
