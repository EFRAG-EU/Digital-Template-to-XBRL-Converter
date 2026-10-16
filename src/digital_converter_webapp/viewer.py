"""Serving the Inline XBRL viewer and its document set members."""

from __future__ import annotations

from flask import Response, request, send_file

from mireport.filesupport import FilelikeAndFileName

# A document set member is content extracted from a user-supplied PDF, served
# same-origin. mireport.pdf_converter strips script out of it; this is the
# second line of that defence, and takes away nothing an annex uses. The origin
# stays 'self' rather than sandboxed so the viewer can still reach into the
# member's DOM, and the viewer itself is served without this — it *is* script.
MEMBER_CSP = (
    "default-src 'none'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "script-src 'none'; "
    "object-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'self'"
)


def serveViewerFile(stuff: FilelikeAndFileName) -> Response:
    if request.method == "HEAD":
        return Response(status=200, headers={"X-File-Ready": "true"})

    return send_file(
        stuff.fileLike(),
        as_attachment=False,
        download_name=stuff.filename,
        mimetype="text/html",
    )
