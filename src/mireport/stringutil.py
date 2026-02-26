_Unicode_Dash_Translation = str.maketrans(
    {
        "\N{EM DASH}": "\N{HYPHEN-MINUS}",
        "\N{EN DASH}": "\N{HYPHEN-MINUS}",
    }
)


def unicodeDashNormalization(label: str) -> str:
    """Clean up a label by replacing dashes with hyphens and removing all
    leading and trailing whitespace (as defined by Unicode)."""
    return label.translate(_Unicode_Dash_Translation).strip()


_Unicode_Category_Zs = (
    "\N{NO-BREAK SPACE}",
    "\N{OGHAM SPACE MARK}",
    "\N{EN QUAD}",
    "\N{EM QUAD}",
    "\N{EN SPACE}",
    "\N{EM SPACE}",
    "\N{THREE-PER-EM SPACE}",
    "\N{FOUR-PER-EM SPACE}",
    "\N{SIX-PER-EM SPACE}",
    "\N{FIGURE SPACE}",
    "\N{PUNCTUATION SPACE}",
    "\N{THIN SPACE}",
    "\N{HAIR SPACE}",
    "\N{NARROW NO-BREAK SPACE}",
    "\N{MEDIUM MATHEMATICAL SPACE}",
    "\N{IDEOGRAPHIC SPACE}",
)


_unicodeSpaceNormalize_Translation_Table = str.maketrans(
    {ch: " " for ch in _Unicode_Category_Zs}
)


def unicodeSpaceNormalize(text: str) -> str:
    """Replace non-breaking and special space separator characters with a
    regular space.

    See https://gitlab.xbrl.org/base-spec/trr/-/issues/16 for details."""
    return text.translate(_unicodeSpaceNormalize_Translation_Table)


def normalizeLabelText(text: str) -> str:
    """Remove extraneous spaces from a label and normalize other characters
    (such as dashes) so that it is easy to compare against the label from
    another tool (or Excel template)."""
    # Replace any run of whitespace (that matches str.isspace()), unicode or
    # ascii, with a single space. Used this way, strip() is also done by the
    # no-args split().
    out = " ".join(text.split())
    out = unicodeDashNormalization(out)
    return out


def stripLabelSuffix(text: str) -> str:
    """Strip any [asdf] suffix from label text."""
    before, sep, _ = text.rpartition("[")
    return stripped if sep and (stripped := before.rstrip()) else text


NumberGroupingApostrophes = frozenset("'`´’′")


def format_time_ns(ns: int) -> str:
    """Formats nanoseconds into human-readable units from ns up to days."""
    US = 10**3  # microseconds
    MS = 10**6  # milliseconds
    S = 10**9  # seconds
    MIN = 60 * S  # one minute in nanoseconds
    HOUR = 60 * MIN  # one hour in nanoseconds
    DAY = 24 * HOUR  # one day in nanoseconds

    match ns:
        case n if n < US:
            return f"{n} ns"
        case n if n < MS:
            return f"{n // US} µs"
        case n if n < S:
            return f"{n // MS} ms"
        case n if n < MIN:
            return f"{n / S:.1f} s"
        case n if n < HOUR:
            return f"{n / MIN:.1f} minutes"
        case n if n < DAY:
            return f"{n / HOUR:.1f} hours"
        case n:
            return f"{n / DAY:.1f} days"


def format_bytes(num_bytes: int) -> str:
    """Formats bytes into KB, MB, or GB using binary prefixes."""
    KB = 2**10
    MB = 2**20
    GB = 2**30

    match num_bytes:
        case n if n < KB:
            return f"{n} B"
        case n if n < MB:
            return f"{n // KB} KiB"
        case n if n < GB:
            return f"{n // MB} MiB"
        case n:
            return f"{n / GB:.1f} GiB"


_xmlCarefulHandling_Translation_Table = str.maketrans(
    {
        "&": "&amp;",
        ">": "&gt;",
        "<": "&lt;",
        "'": "&apos;",
        '"': "&quot;",
        "\v": None,
        "\t": None,
        "\f": None,
        "\r": None,
        "\n": None,
    }
)


def xml_clean(data: str) -> str:
    """Cleans a string for inclusion in XML content by escaping special characters
    and removing vertical tabs, tabs, form-feeds, carriage returns, and newlines."""
    return data.translate(_xmlCarefulHandling_Translation_Table)
