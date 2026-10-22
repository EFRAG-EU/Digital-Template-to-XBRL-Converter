"""Running mammoth on a Word document."""

from __future__ import annotations

import mammoth
from markupsafe import Markup

from mireport.docx_converter._messages import DocxConversion
from mireport.filesupport import FilelikeAndFileName


def convertDocxToHtml(docx: FilelikeAndFileName) -> DocxConversion:
    """Convert the .docx @docx to HTML."""
    result = mammoth.convert_to_html(docx.fileLike())
    return DocxConversion(
        filename=docx.filename,
        html=Markup(result.value),
        messages=[message.message for message in result.messages],
    )
