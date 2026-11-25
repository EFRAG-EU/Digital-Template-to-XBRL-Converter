from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

if TYPE_CHECKING:
    from openpyxl import Workbook

    from mireport.filesupport import FilelikeAndFileName


class Workbooks:
    """The uploaded workbooks a request has parsed, so that one checked for
    migration is converted without being parsed again.

    Known by what they hold, not their names. A file that can't be parsed is
    tried again each time, so whatever reads it next says what is wrong with
    it in its own way.
    """

    __slots__ = ("_parsed",)

    def __init__(self) -> None:
        self._parsed: dict[bytes, Workbook] = {}

    def load(self, upload: FilelikeAndFileName) -> Workbook:
        """The workbook, kept for whatever reads it next."""
        key = _key(upload)
        if (workbook := self._parsed.get(key)) is None:
            workbook = loadExcelFromPathOrFileLike(upload.fileLike())
            self._parsed[key] = workbook
        return workbook

    def take(self, upload: FilelikeAndFileName) -> Workbook:
        """The workbook, no longer kept: for its last reader, so that it is freed
        once read rather than at the end of the request."""
        if (workbook := self._parsed.pop(_key(upload), None)) is None:
            workbook = loadExcelFromPathOrFileLike(upload.fileLike())
        return workbook


def _key(upload: FilelikeAndFileName) -> bytes:
    return hashlib.sha256(upload.fileContent).digest()
