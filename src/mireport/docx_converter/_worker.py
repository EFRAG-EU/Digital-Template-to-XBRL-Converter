"""Converts the .docx on stdin, writing the HTML and mammoth's messages to stdout
as JSON.

:func:`mireport.docx_converter.convertDocxToHtml` runs this file by its path in
a child process it can kill, so it imports only the standard library and
mammoth, never the mireport package.
"""

import json
import sys
from io import BytesIO

import mammoth


def main() -> None:
    result = mammoth.convert_to_html(BytesIO(sys.stdin.buffer.read()))
    json.dump(
        {
            "html": result.value,
            "messages": [message.message for message in result.messages],
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
