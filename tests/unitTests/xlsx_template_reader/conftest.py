import pytest

from mireport.taxonomy import loadBuiltInTaxonomyJSON


@pytest.fixture(scope="session", autouse=True)
def _load_taxonomies():
    loadBuiltInTaxonomyJSON()
