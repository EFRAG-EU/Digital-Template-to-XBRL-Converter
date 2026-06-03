from importlib.resources import files
from json import loads

VSME_DEFAULTS: dict = loads(files(__name__).joinpath("vsme.json").read_bytes())
