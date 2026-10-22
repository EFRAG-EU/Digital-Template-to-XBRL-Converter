"""Converts the .docx on stdin, writing the HTML and mammoth's messages to stdout
as JSON.

:func:`mireport.docx_converter.convertDocxToHtml` runs this file by its path in
a child process it can kill, so it imports only the standard library and
mammoth, never the mireport package. In-process, it calls :func:`convert`.
"""

import json
import sys
from io import BytesIO
from typing import IO

import mammoth


def convert(docx: IO[bytes]) -> tuple[str, list[str]]:
    """mammoth's HTML for @docx and its messages about it."""
    result = mammoth.convert_to_html(docx)
    return result.value, [message.message for message in result.messages]


def main() -> None:
    html, messages = convert(BytesIO(sys.stdin.buffer.read()))
    json.dump({"html": html, "messages": messages}, sys.stdout)


if __name__ == "__main__":
    main()
