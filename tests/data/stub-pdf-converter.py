"""Stands in for a PDF-to-HTML converter binary in tests.

Neither supported converter is guaranteed to be installed but monkeypatching
subprocess.run only ever tests our own argv construction, never the round trip
through a real process, which is where argument quoting, exit codes, encoding
and timeouts actually bite. So the tests run *this* through the same subprocess
call instead.

Emulates whichever command line it is given. The two differ in ways that matter:
pdf2htmlEX takes --dest-dir plus a bare output name and inlines its own assets;
pdftohtml takes an output path and writes page images beside it, which the
converter has to inline afterwards.

Behaviour is switched by the STUB_BEHAVIOUR environment variable:

    ok       (default) write plausible converter output and exit 0 fail
    write a diagnostic to stderr and exit 3 hang     sleep past any test's
    timeout, but not indefinitely empty    exit 0 without writing anything
    garbage  write bytes that are not valid HTML at all
"""

import base64
import os
import sys
import time
from pathlib import Path

HANG_SECONDS = 5

# A 1x1 PNG, standing in for a rendered page image.
PAGE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)

# pdf2htmlEX emits HTML5, not XHTML: bare doctype, <meta charset>, a <script>
# whose content an XML parser rejects, and data-* attributes. Unclosed void
# elements and unquoted attribute values are the stub being harsher than the
# real thing. Reproducing that shape here is the point — it is what the
# normaliser has to cope with, and every bit of it was a real XHTML schema
# error at some point.
PDF2HTMLEX_OUTPUT = """<!DOCTYPE html>
<html><head><meta charset=utf-8>
<style type="text/css">
.pf{position:relative;background-color:white;overflow:hidden}
.c{position:absolute;border:0;height:100%}
</style>
<script>
if (1 < 2 && 3 > 2) { var pdf2htmlEX = window.pdf2htmlEX || {}; }
</script>
</head>
<body>
<div id="page-container">
<div class="pf" data-page-no=1>
<div class="c">
<div class="t">Converted annex &amp; appendix</div>
<img alt="" src="data:image/png;base64,iVBORw0KGgo=">
<br>
<hr>
<p>Unclosed paragraph
</div>
</div>
</div>
</body>
</html>
"""

# pdftohtml's output is closer to XHTML already, but references its page images
# by relative path — this build of Poppler has no -dataurls option. Copied from
# real output; every oddity below was a real Arelle schema error:
#   - lang alongside xml:lang; only the latter is allowed
#   - bgcolor/link/vlink on body, which are Transitional and not Strict
#   - <a name="1"> page anchors: Strict has no name attribute on <a>, and bare
#     inline content directly in the body is "not expected"
#   - a <style> element in the body, which Strict allows only in the head
PDFTOHTML_OUTPUT = """<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="" xml:lang="">
<head>
<title>probe</title>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8"/>
<meta name="generator" content="pdftohtml 0.36"/>
</head>
<body bgcolor="#A0A0A0" vlink="blue" link="blue">
<!-- Page 1 -->
<a name="1"></a>
<style type="text/css">
<!--
	p {{margin: 0; padding: 0;}}-->
</style>
<div id="page1-div" style="position:relative;width:918px;height:1188px;">
<img width="918" height="1188" src="{stem}001.png" alt="Converted annex &amp; appendix"/>
</div>
<!-- Page 2 -->
<a name="2"></a>
<div id="page2-div" style="position:relative;width:918px;height:1188px;">
<img width="918" height="1188" src="{stem}002.png" alt="background image"/>
<a href="#1">back to page 1</a>
</div>
</body>
</html>
"""


def main() -> int:
    behaviour = os.environ.get("STUB_BEHAVIOUR", "ok")

    if behaviour == "hang":
        # Deliberately short. On Windows this stub runs behind a .cmd shim, so
        # subprocess.run's timeout kills cmd.exe but not this process, and the
        # inherited pipes keep communicate() blocked until it exits anyway.
        # (A real converter is exec'd directly, so its pipes close on kill.)
        time.sleep(HANG_SECONDS)
        return 0

    if behaviour == "fail":
        print("stub converter: cannot read PDF", file=sys.stderr)
        return 3

    args = sys.argv[1:]
    isPdf2HtmlEx = "--dest-dir" in args

    if isPdf2HtmlEx:
        destDir = Path(args[args.index("--dest-dir") + 1])
        outputPath = destDir / args[-1]
        content = PDF2HTMLEX_OUTPUT.encode("utf-8")
        images: dict[str, bytes] = {}
    else:
        outputPath = Path(args[-1])
        stem = outputPath.stem
        content = PDFTOHTML_OUTPUT.format(stem=stem).encode("utf-8")
        images = {f"{stem}001.png": PAGE_PNG, f"{stem}002.png": PAGE_PNG}

    if behaviour == "empty":
        return 0
    if behaviour == "garbage":
        content, images = b"\x00\x01not html at all", {}

    outputPath.parent.mkdir(parents=True, exist_ok=True)
    outputPath.write_bytes(content)
    for name, data in images.items():
        (outputPath.parent / name).write_bytes(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
