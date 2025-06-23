from importlib.abc import Traversable
from importlib.resources import Package, files
from json import loads
from typing import Any, Generator

__all__ = ["getResource", "getObject", "getJsonFiles"]


def getResource(module: Package, filename: str) -> Traversable:
    return files(module).joinpath(filename)


def getObject(source: Traversable) -> Any:
    return loads(source.read_bytes())


def getJsonFiles(module: Package) -> Generator[Traversable, None, None]:
    for f in files(module).iterdir():
        if f.is_file() and f.name.endswith(".json"):
            yield f
