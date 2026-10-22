"""Turning HTML from other tools into XHTML that Inline XBRL accepts.

Two things stand between a converter's output and a document that can join the
tagged report in an Inline XBRL Document Set:

1. **Well-formedness.** iXBRL 1.1 defines an Inline XBRL Document as a
   well-formed XML document (spec 3.1), and a Validating Conformant Processor
   accepts only documents rooted in ``{xhtml}html`` and valid against the
   XHTML + iXBRL schema (spec 3.3.1). Neither PDF converter's output survives
   an XML parser. pdf2htmlEX is the closer of the two — it self-closes its void
   elements and declares the XHTML namespace already — but it serves HTML5,
   whose content model lets a raw ``<`` sit inside ``<script>``, and its own
   copyright banner does exactly that. So output is re-parsed leniently and
   re-serialised as XHTML.

2. **Inline XBRL identity.** The same definition requires an Inline XBRL
   Document to contain Inline XBRL Elements, so a member declaring ``xmlns:ix``
   and nothing else is not one, and a processor drops it from the set — Arelle
   says so with an ``arelle:nonIxdsDocument`` warning, and the member would then
   travel in the zip but never reach the viewer. So an empty ``ix:header`` is
   injected, inside a ``display:none`` div as spec 8.1.2 recommends.
   It declares no facts, no contexts, no units and no schema references: the
   annex contributes page content, and the tagged report remains the sole source
   of XBRL data. Only one ``ix:header`` need carry anything across the whole set
   (spec 8.1.3), and the tagged report supplies it.

Script is removed outright, and that means every form of it: the ``<script>``
element (:func:`._hygiene._stripScripts`), event handler attributes and
``javascript:`` URLs (:func:`._hygiene._stripScripting`). The web app serves
document set members same-origin, so script surviving in an annex built from an
uploaded PDF would run with the user's session.

Everything else here repairs a specific thing a real converter emits that the
XHTML *Strict* half of that schema rejects; none of it is precautionary, and
``tests/integrationTests/test_real_pdf_converter.py`` is what proves it. What is
*not* repaired is refused: an element outside XHTML Strict fails the conversion
rather than being translated on a guess — see
:func:`._hygiene._rejectUnknownElements`.

A document is parsed once (:func:`parseDocument`), normalised in place
(:func:`normaliseDocument`) and handed along as a tree; only what leaves for a
package is serialised (:func:`documentToBytes`). The rules for single elements
are in :mod:`._hygiene`; assembling a whole document — its head, its body and
the ``ix:header`` — is :mod:`._document`. A fragment that joins a report, such
as a Word text block, gets the rules but not the assembly
(:func:`normaliseFragment`).
"""

from __future__ import annotations

from mireport.html_to_xhtml._document import documentToBytes, normaliseDocument
from mireport.html_to_xhtml._exceptions import XhtmlError
from mireport.html_to_xhtml._fragment import normaliseFragment
from mireport.html_to_xhtml._parse import parseDocument
from mireport.html_to_xhtml._tree import IX_NAMESPACE, XHTML_NAMESPACE

__all__ = [
    "IX_NAMESPACE",
    "XHTML_NAMESPACE",
    "XhtmlError",
    "documentToBytes",
    "normaliseDocument",
    "normaliseFragment",
    "parseDocument",
]
