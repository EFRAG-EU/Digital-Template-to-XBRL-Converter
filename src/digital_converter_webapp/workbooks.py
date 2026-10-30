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
        key = hashlib.sha256(upload.fileContent).digest()
        if (workbook := self._parsed.get(key)) is None:
            workbook = loadExcelFromPathOrFileLike(upload.fileLike())
            self._parsed[key] = workbook
        return workbook
