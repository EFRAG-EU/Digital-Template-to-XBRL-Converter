"""Word documents and a workbook for tests of text blocks whose values come from Word.

``wordDocument`` builds a minimal .docx in memory. ``externalValuesWorkbook`` is the 1.3.0 sample
with ``EXTERNAL_CONCEPT``, a text block it reports, named in a ``template_external_values`` range,
since no shipped template uses one.
"""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from openpyxl.workbook.defined_name import DefinedName

from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

SAMPLE_1_3_0 = (
    Path(__file__).parents[2]
    / "digital-templates"
    / "VSME-Digital-Template-Sample-1.3.0.xlsx"
)
EXTERNAL_CONCEPT = "vsme:DescriptionOfATargetRelatedToAPolicy"

_CONTENT_TYPES = (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
_NAMESPACES = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
_DRAWING = (
    '<w:drawing><wp:inline><wp:docPr id="1" name="Picture 1" descr="{altText}"/>'
    '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    '<pic:pic><pic:blipFill><a:blip r:embed="rIdImage"/></pic:blipFill></pic:pic>'
    "</a:graphicData></a:graphic></wp:inline></w:drawing>"
)
_DOCUMENT_RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rIdImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
    "</Relationships>"
)


def wordDocument(
    text: str,
    *,
    paragraphStyle: str | None = None,
    breakType: str | None = None,
    image: bool = False,
    imageAltText: str = "",
) -> bytes:
    """A .docx of one paragraph. A ``paragraphStyle`` is not defined in the document, so mammoth
    warns about it; a ``breakType`` it doesn't support, mammoth warns about and drops. An ``image``
    follows the text, with ``imageAltText`` as its Word alt text."""
    style = (
        f'<w:pPr><w:pStyle w:val="{paragraphStyle}"/></w:pPr>' if paragraphStyle else ""
    )
    lineBreak = f'<w:br w:type="{breakType}"/>' if breakType else ""
    drawing = _DRAWING.format(altText=imageAltText) if image else ""
    document = (
        f"<w:document {_NAMESPACES}>"
        f"<w:body><w:p>{style}<w:r><w:t>{text}</w:t>{lineBreak}{drawing}</w:r></w:p></w:body>"
        "</w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as docx:
        docx.writestr("[Content_Types].xml", _CONTENT_TYPES)
        docx.writestr("_rels/.rels", _RELS)
        docx.writestr("word/document.xml", document)
        if image:
            docx.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS)
            docx.writestr("word/media/image1.png", b"not really a png")
    return buffer.getvalue()


def externalValuesWorkbook() -> bytes:
    workbook = loadExcelFromPathOrFileLike(SAMPLE_1_3_0)
    sheet = workbook.worksheets[0]
    cell = sheet.cell(row=1, column=250)  # far out, otherwise unused
    cell.value = EXTERNAL_CONCEPT.partition(":")[2]
    workbook.defined_names.add(
        DefinedName(
            "template_external_values", attr_text=f"'{sheet.title}'!{cell.coordinate}"
        )
    )
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()
