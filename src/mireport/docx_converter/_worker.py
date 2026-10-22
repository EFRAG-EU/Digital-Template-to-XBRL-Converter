"""Converts the .docx on stdin, writing the HTML and mammoth's messages to stdout
as JSON.

:func:`mireport.docx_converter.convertDocxToHtml` runs this file by its path in
a child process it can kill, so it imports only the standard library and
mammoth, never the mireport package. In-process, it calls :func:`convert`.
"""

import base64
import json
import sys
from io import BytesIO
from typing import IO, Any

import mammoth


@mammoth.images.img_element
def _imageWithAlt(image: Any) -> dict[str, str]:
    """An image as a data: URI, as mammoth's default does, but always with an
    ``alt``: XHTML requires one. Word's alt text when the image has some."""
    with image.open() as imageBytes:
        encoded = base64.b64encode(imageBytes.read()).decode("ascii")
    return {
        "src": f"data:{image.content_type};base64,{encoded}",
        "alt": image.alt_text or "",
    }


def convert(docx: IO[bytes]) -> tuple[str, list[str]]:
    """The HTML for @docx and mammoth's messages about it."""
    result = mammoth.convert_to_html(docx, convert_image=_imageWithAlt)
    return result.value, [message.message for message in result.messages]


def main() -> None:
    html, messages = convert(BytesIO(sys.stdin.buffer.read()))
    json.dump({"html": html, "messages": messages}, sys.stdout)


if __name__ == "__main__":
    main()
