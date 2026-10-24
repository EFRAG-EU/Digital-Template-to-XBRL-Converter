import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from markupsafe import Markup

import mireport
import mireport.taxonomy
from mireport.arelle.report_info import (
    ARELLE_VERSION_INFORMATION,
    ArelleReportProcessor,
)
from mireport.cli import (
    addThemeImages,
    configure_rich_output,
    printSections,
    validateTaxonomyPackages,
)
from mireport.cli import (
    console_print as print,
)
from mireport.comparatives import PriorReport
from mireport.conversionresults import (
    ConversionResults,
    ConversionResultsBuilder,
    MessageType,
    Severity,
    Stage,
)
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.docx_converter import DocxConversionError, convertDocxToHtml
from mireport.entrypoints import describeEntryPointSet
from mireport.exceptions import EarlyAbortException
from mireport.filesupport import FilelikeAndFileName
from mireport.localise import EU_LOCALES, argparse_locale
from mireport.pdf_converter import (
    PdfSupplementaryMode,
    convertSupplementaryPdfs,
    mergeAnnexesIntoReport,
)
from mireport.report.reportpackage import buildReportPackage
from mireport.report.theme import ColourPalette, DisplayMode, ReportTheme
from mireport.xbrljson_writer import toXbrlJson
from mireport.xlsx_template_reader.processor import XlsxProcessor


def _convert_docx_to_markup(
    docx_path: Path, resultsBuilder: ConversionResultsBuilder, idPrefix: str
) -> Markup:
    """Convert a .docx file to HTML and return it as Markup, reporting anything
    that could not be converted. Its ids start @idPrefix, unique to it. One
    that can't be converted at all stops the conversion."""
    try:
        conversion = convertDocxToHtml(
            FilelikeAndFileName(docx_path.read_bytes(), docx_path.name),
            idPrefix=idPrefix,
        )
    except DocxConversionError as e:
        resultsBuilder.addMessage(
            str(e), Severity.ERROR, MessageType.Conversion, source=e.filename
        )
        raise EarlyAbortException(str(e)) from e
    conversion.addTo(resultsBuilder)
    return conversion.html


class ExtraDataFileError(FileNotFoundError):
    """A file named in the --extra-data JSON is missing: a mistake in the
    input, reported as a usage error rather than a crash."""


def _resolve_extra_path(extra_file: Path, relative_path: str) -> Path:
    """Resolve a relative path from extra_data JSON and verify the file exists."""
    resolved = extra_file.parent / relative_path
    if not resolved.is_file():
        raise ExtraDataFileError(f"Referenced file does not exist: {resolved}")
    return resolved


def createArgParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract facts from Excel file and generate HTML."
    )
    parser.add_argument("xlsx_file", type=Path, help="Path to the Excel file")
    parser.add_argument(
        "output_path",
        type=Path,
        help="Path to save the output. Can be a directory or a file. Automatically creates directories and warns before overwriting files.",
    )
    parser.add_argument(
        "--output-locale",
        type=argparse_locale,
        default=None,
        help=f"Locale to use when formatting the output XBRL report. Examples:\n{sorted(EU_LOCALES)}",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Suppress overwrite warnings and force file replacement.",
    )
    parser.add_argument(
        "--devinfo",
        action=argparse.BooleanOptionalAction,
        help="Enable display of developer information issues (not normally visible to users)",
    )
    parser.add_argument(
        "--taxonomy-packages",
        type=str,
        nargs="+",
        default=[],
        help="Paths to the taxonomy packages to be used (globs such as *.zip, and directories of zips, are permitted).",
    )
    parser.add_argument(
        "--offline",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="All work is done offline. Default is to work online, that is --no-offline ",
    )
    parser.add_argument(
        "--skip-validation",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Disables XBRL validation. Useful during development only.",
    )
    parser.add_argument(
        "--viewer",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Generate a viewer as well.",
    )
    parser.add_argument(
        "--json",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Write the report as xBRL-JSON as well (beside the output, with a .json "
        "suffix), validated by Arelle unless --skip-validation.",
    )
    parser.add_argument(
        "--style-mode",
        type=DisplayMode,
        choices=list(DisplayMode),
        default=ReportTheme.DEFAULT_DISPLAY_MODE,
        help="Report colour mode (default: %(default)s).",
    )
    palette_group = parser.add_mutually_exclusive_group()
    palette_group.add_argument(
        "--style-preset",
        choices=ColourPalette.labels(),
        default=ReportTheme.DEFAULT_COLOUR.label,
        help="Report colour preset (default: %(default)s).",
    )
    palette_group.add_argument(
        "--style-custom",
        metavar="#RRGGBB",
        default=None,
        help="Custom report accent colour as a 6-digit hex code (e.g. #1a2b3c).",
    )
    parser.add_argument(
        "--image-logo",
        type=Path,
        default=None,
        help="Path to an image file to use as the entity logo.",
    )
    parser.add_argument(
        "--image-cover",
        type=Path,
        default=None,
        help="Path to an image file to use as the cover page image.",
    )
    parser.add_argument(
        "--image-background",
        type=Path,
        default=None,
        help="Path to an image file to use as a background image on report pages.",
    )
    parser.add_argument(
        "--extra-data",
        type=Path,
        default=None,
        help="Path to a JSON file containing extra report data (footnotes, label overrides, etc.).",
    )
    parser.add_argument(
        "--pdf-mode",
        type=PdfSupplementaryMode,
        choices=list(PdfSupplementaryMode),
        default=PdfSupplementaryMode.DOCSET,
        help=(
            "How a successfully converted supplementary PDF (see --extra-data's "
            "pdfAttachments) joins the report: as its own Inline XBRL document "
            "set member ('docset', the default), or merged into the back of "
            "the report document itself ('merge'). A PDF that fails to "
            "convert is always attached to the package unconverted, "
            "regardless of this setting."
        ),
    )
    parser.add_argument(
        "--prior",
        type=Path,
        default=None,
        help="Last year's report (xBRL-JSON, a Digital Template workbook, Inline XBRL or a report package), whose figures become the comparatives.",
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        help="Turn on debugging output.",
    )
    return parser


def parseArgs(parser: argparse.ArgumentParser) -> argparse.Namespace:
    args = parser.parse_args()
    if args.offline and not args.taxonomy_packages:
        parser.error(
            "You need to specify --taxonomy-packages if you want to work offline"
        )
    if args.taxonomy_packages:
        args.taxonomy_packages = validateTaxonomyPackages(
            args.taxonomy_packages, parser
        )
    if args.debug:
        logging.getLogger("mireport").setLevel(logging.DEBUG)
    return args


def prepare_output_path(path: Path, force: bool) -> tuple[Path, bool]:
    if path.exists():
        if path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            return path, True
        else:
            if not force:
                print(f"⚠️ Warning: Overwriting existing file: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            return path, False
    else:
        if path.suffix:
            # Treat as file
            path.parent.mkdir(parents=True, exist_ok=True)
            return path, False
        else:
            # Treat as directory
            path.mkdir(parents=True, exist_ok=True)
            return path, True


def doConversion(args: argparse.Namespace) -> tuple[ConversionResults, list[str]]:
    resultsBuilder = ConversionResultsBuilder(consoleOutput=True)
    unused: list[str] = []
    arp = ArelleReportProcessor(
        taxonomyPackages=args.taxonomy_packages,
        workOffline=args.offline,
    )
    with resultsBuilder.processingContext(
        "mireport Excel to validated Inline Report"
    ) as pc:
        with resultsBuilder.section(Stage.TAXONOMY):
            mireport.loadBuiltInTaxonomyJSON()
            allTaxonomies = mireport.taxonomy.listTaxonomies()
            pc.addDevInfoMessage(
                f"Taxonomies entry points ({len(allTaxonomies)}) available: "
                + ", ".join(describeEntryPointSet(eps) for eps in allTaxonomies)
            )
            resultsBuilder.addMessage(
                f"{len(allTaxonomies)} taxonomy entry points loaded.",
                Severity.INFO,
                MessageType.Conversion,
            )
        with resultsBuilder.section(Stage.WORKBOOK, source=args.xlsx_file.name):
            xl_processor = XlsxProcessor.from_file(
                args.xlsx_file,
                resultsBuilder,
                VSME_DEFAULTS,
                outputLocale=args.output_locale,
            )
            report = xl_processor.createReport()
        if args.prior:
            prior = FilelikeAndFileName(args.prior.read_bytes(), args.prior.name)
            with resultsBuilder.section(Stage.PRIOR, source=prior.filename):
                PriorReport.fromFile(prior, resultsBuilder, arelle=arp).mergeInto(
                    report, resultsBuilder
                )

        colour = ColourPalette.parse(args.style_custom or args.style_preset)
        report.theme.setDisplayMode(args.style_mode).setColour(colour)

        images = [
            (arg_name, image_path, imageSetter)
            for arg_name, imageSetter in [
                ("image_logo", report.theme.setLogoImage),
                ("image_cover", report.theme.setCoverImage),
                ("image_background", report.theme.setBackgroundImage),
            ]
            if (image_path := getattr(args, arg_name))
        ]
        if images:
            with resultsBuilder.section(Stage.IMAGES):
                addThemeImages(images, resultsBuilder)

        supplementary_pdfs: list[FilelikeAndFileName] = []
        if (extra_file := args.extra_data) and extra_file.is_file():
            extra = json.loads(extra_file.read_text(encoding="utf-8"))

            for fn in extra.get("footnotes", []):
                concept_strs: list[str] = list(fn.get("concepts") or [])
                if single := fn.get("concept"):
                    concept_strs.append(single)
                concepts = [
                    c
                    for s in concept_strs
                    if (
                        c := report.taxonomy.resolveConcept(
                            s, by_qname=True, by_label=True
                        )
                    )
                    is not None
                ]
                report.addFootnoteForConcepts(
                    Markup(fn.get("content", "")), concepts, group=fn.get("group")
                )

            label_overrides = {
                lo["concept"]: lo["label"] for lo in extra.get("labelOverrides", [])
            }
            if label_overrides:
                report.setLabelOverrides(label_overrides)

            optional_section_setters = {
                "introduction": report.setIntroduction,
                "backCoverMatter": report.setBackCoverMatter,
            }
            # Every Word document, in one section.
            with resultsBuilder.section(Stage.WORD):
                for section in extra.get("optionalSections", []):
                    section_id = section.get("id")
                    sectionSetter = optional_section_setters.get(section_id)
                    if sectionSetter is None:
                        pc.addDevInfoMessage(
                            f"Unknown optionalSections id: {section_id!r}"
                        )
                        continue
                    if "path" in section:
                        docx_path = _resolve_extra_path(extra_file, section["path"])
                        sectionSetter(
                            _convert_docx_to_markup(
                                docx_path, resultsBuilder, idPrefix=f"{section_id}-"
                            )
                        )
                    elif "content" in section:
                        sectionSetter(Markup(section["content"]))
                    else:
                        pc.addDevInfoMessage(
                            f"optionalSections entry {section_id!r} has neither 'path' nor 'content'"
                        )

                for rtv in extra.get("replacementTextblockValues", []):
                    docx_path = _resolve_extra_path(extra_file, rtv["path"])
                    concept = report.taxonomy.getConcept(rtv["concept"])
                    markup = _convert_docx_to_markup(
                        docx_path,
                        resultsBuilder,
                        idPrefix=f"{concept.qname.localName}-",
                    )
                    # A text block the template marks as external has no fact to
                    # replace yet, only a partial one waiting for this value.
                    if concept in report.partialFactsByConcept:
                        report.completePartialFact(concept, markup)
                    else:
                        report.replaceFactValue(concept, markup)

            for entry in extra.get("pdfAttachments", []):
                pdf_path = _resolve_extra_path(extra_file, entry["path"])
                supplementary_pdfs.append(
                    FilelikeAndFileName(
                        fileContent=pdf_path.read_bytes(), filename=pdf_path.name
                    )
                )

        pdfs = convertSupplementaryPdfs(
            supplementary_pdfs, resultsBuilder, pc, mode=args.pdf_mode
        )

        with resultsBuilder.section(Stage.REPORT):
            reportFile = report.getInlineReport()
            report.reportUntranslatedStrings(pc)
            if pdfs.mergedAnnexes:
                reportFile = mergeAnnexesIntoReport(reportFile, pdfs.mergedAnnexes)
            reportPackage = buildReportPackage(
                reportFile,
                topLevel=report.packageTopLevelName,
                docsetMembers=pdfs.docsetMembers,
                attachments=pdfs.attachments,
            )

            output_path, dir_specified = prepare_output_path(
                args.output_path, args.force
            )
            if dir_specified:
                pc.addDevInfoMessage(
                    f"Writing various files to {output_path} ({report.factCount} facts to include)"
                )
                reportFile.saveToDirectory(output_path)
                reportPackage.saveToDirectory(output_path)
            else:
                pc.addDevInfoMessage(
                    f"Writing {reportFile} to {output_path} ({report.factCount} facts to include)"
                )
                reportFile.saveToFilepath(output_path)
            resultsBuilder.addMessage(
                f"Inline XBRL report {reportPackage} written to {output_path} "
                f"(containing {report.factCount} facts).",
                Severity.INFO,
                MessageType.Conversion,
            )

        xbrlJson: FilelikeAndFileName | None = None
        if args.json:
            # Written from the report itself; Arelle checks it below.
            jsonPath = (
                output_path / Path(reportPackage.filename).with_suffix(".json").name
                if dir_specified
                else output_path.with_suffix(".json")
            )
            with resultsBuilder.section(Stage.XBRL_JSON_OUTPUT, source=jsonPath.name):
                xbrlJson = FilelikeAndFileName(
                    fileContent=toXbrlJson(report).toJson().encode("utf-8"),
                    filename=jsonPath.name,
                )
                xbrlJson.saveToFilepath(jsonPath)
                resultsBuilder.addMessage(
                    f"Written to {jsonPath} (containing {report.factCount} facts).",
                    Severity.INFO,
                    MessageType.Conversion,
                )

        if args.skip_validation:
            resultsBuilder.skip(Stage.PACKAGE, "--skip-validation was given.")
            if xbrlJson is not None:
                resultsBuilder.skip(Stage.XBRL_JSON, "--skip-validation was given.")
        else:
            with resultsBuilder.section(Stage.PACKAGE):
                pc.addDevInfoMessage(
                    f"Validating using Arelle ({ARELLE_VERSION_INFORMATION})"
                )
                pc.addDevInfoMessage(f"Using Inline Report package: {reportPackage}")

                if not args.viewer:
                    arelleResults = arp.validateReportPackage(reportPackage)
                    resultsBuilder.addMessages(arelleResults.messages)

                if args.viewer:
                    arelleResults = arp.generateInlineViewer(reportPackage)
                    resultsBuilder.addMessages(arelleResults.messages)

                    if arelleResults.has_viewer:
                        viewer = arelleResults.viewer
                        if not dir_specified:
                            viewer.saveToFilepath(output_path)
                        else:
                            viewer.saveToDirectory(output_path)
                        resultsBuilder.addMessage(
                            f"Viewer {viewer} written to {output_path}.",
                            Severity.INFO,
                            MessageType.Conversion,
                        )
                    else:
                        pc.addDevInfoMessage("Failed to create viewer.")
                resultsBuilder.addMessages(
                    [arelleResults.summary(reportPackage.filename)]
                )

            if xbrlJson is not None:
                with resultsBuilder.section(Stage.XBRL_JSON):
                    arelleResults = arp.validateXbrlJson(xbrlJson)
                    resultsBuilder.addMessages(
                        [
                            *arelleResults.messages,
                            arelleResults.summary(xbrlJson.filename),
                        ]
                    )
        unused = xl_processor.unusedNames
    return resultsBuilder.build(), unused


def outputMessages(
    args: argparse.Namespace, result: ConversionResults, unused: list[str]
) -> None:
    hasMessages = result.hasMessages(userOnly=True)
    messages = result.userMessages
    if args.devinfo:
        hasMessages = result.hasMessages()
        messages = result.developerMessages

    if hasMessages:
        print()
        print(
            f"Information and issues encountered ({len(messages)} message{('s' if len(messages) != 1 else '')}):"
        )
        printSections(result.sections, developer=bool(args.devinfo))

    if args.devinfo and unused:
        max_output = 40
        if (num := len(unused)) > max_output:
            size = int(max_output / 2)
            unused = (
                unused[:size]
                + [f"... supressed {num - max_output} rows..."]
                + unused[-size:]
            )

        print(
            f"Unused names ({num}) from Excel workbook:",
            *unused,
            sep="\n\t",
        )


def main() -> None:
    # For PDF_CONVERTER_PATH, so a checkout converts PDFs the way its web app
    # does rather than needing the same thing on PATH.
    load_dotenv()
    parser = createArgParser()
    args = parseArgs(parser)
    try:
        result, excel = doConversion(args)
    except ExtraDataFileError as e:
        parser.error(str(e))
    outputMessages(args, result, excel)


if __name__ == "__main__":
    configure_rich_output()
    main()
