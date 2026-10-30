"""DocxConversion.addTo: which of mammoth's messages reach the user."""

from markupsafe import Markup

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.docx_converter import DocxConversion

STYLE = "Unrecognised paragraph style: Body Text (Style ID: BodyText)"
UNDEFINED = (
    "Paragraph style with ID Fancy was referenced but not defined in the document"
)
BREAK = "Unsupported break type: bogus"
ELEMENT = "An unrecognised element was ignored: w:foo"


def added(*messages: str) -> list[tuple[str, Severity, MessageType]]:
    builder = ConversionResultsBuilder()
    DocxConversion("policy.docx", Markup(""), list(messages)).addTo(builder)
    assert all(m.source == "policy.docx" for m in builder.messages)
    return [(m.messageText, m.severity, m.messageType) for m in builder.messages]


def test_says_it_was_converted():
    assert added() == [("Converted.", Severity.INFO, MessageType.Conversion)]


def test_style_messages_are_developer_information_counted_once_each():
    assert added(STYLE, UNDEFINED, STYLE)[1:] == [
        (f"{STYLE} (2 times)", Severity.INFO, MessageType.DevInfo),
        (UNDEFINED, Severity.INFO, MessageType.DevInfo),
    ]


def test_other_messages_are_one_warning_in_order_and_counted():
    prefix = "Some content may not appear as it does in Word"
    assert added(ELEMENT, STYLE, BREAK, ELEMENT, ELEMENT)[-1] == (
        f"{prefix}: {ELEMENT} (3 times); {BREAK}",
        Severity.WARNING,
        MessageType.Conversion,
    )


def test_only_style_messages_means_no_warning():
    assert all(severity is Severity.INFO for _, severity, _ in added(STYLE, UNDEFINED))
