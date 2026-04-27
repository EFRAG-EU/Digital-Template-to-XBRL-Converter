import argparse
import json
import logging
from pathlib import Path

import mammoth
import rich.traceback
from markupsafe import Markup
from rich.logging import RichHandler

import mireport
import mireport.taxonomy
from mireport.arelle.report_info import (
    ARELLE_VERSION_INFORMATION,
    ArelleReportProcessor,
)
from mireport.cli import validateTaxonomyPackages
from mireport.conversionresults import (
    ConversionResults,
    ConversionResultsBuilder,
    ProcessingContext,
)
from mireport.xlsx_template_reader.processor import (
    VSME_DEFAULTS,
    ExcelProcessor,
)
from mireport.filesupport import ImageFileLikeAndFileName
from mireport.localise import EU_LOCALES, argparse_locale
from mireport.report.theme import ColourPalette, DisplayMode, ReportTheme


def _convert_docx_to_markup(docx_path: Path, pc: ProcessingContext) -> Markup:
    """Convert a .docx file to HTML via mammoth and return as Markup."""
    with open(docx_path, "rb") as f:
        result = mammoth.convert_to_html(f)
    for msg in result.messages:
        pc.addDevInfoMessage(f"mammoth ({docx_path.name}): {msg}")
    return Markup(result.value)


def _resolve_extra_path(extra_file: Path, relative_path: str) -> Path:
    """Resolve a relative path from extra_data JSON and verify the file exists."""
    resolved = extra_file.parent / relative_path
    if not resolved.is_file():
        raise FileNotFoundError(f"Referenced file does not exist: {resolved}")
    return resolved


def createArgParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract facts from Excel and generate HTML."
    )
    parser.add_argument("excel_file", type=Path, help="Path to the Excel file")
    parser.add_argument(
        "output_path",
        type=Path,
        help="Path to save the output. Can be a directory or a file. Automatically creates directories and warns before overwriting files.",
    )
    parser.add_argument(
        "--output-locale",
        type=argparse_locale,
        default=None,
        help=f"Locale to use when formatting the output XBRL report (default: None). Examples:\n{sorted(EU_LOCALES)}",
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
        help="Paths to the taxonomy packages to be used (globs, *.zip, are permitted).",
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
        help="Generate JSON output as well.",
    )
    parser.add_argument(
        "--style-mode",
        type=DisplayMode,
        choices=list(DisplayMode),
        default=ReportTheme.DEFAULT_DISPLAY_MODE,
        help=f"Report colour mode (default: {ReportTheme.DEFAULT_DISPLAY_MODE}).",
    )
    parser.add_argument(
        "--style-palette",
        choices=ColourPalette.labels(),
        default=ReportTheme.DEFAULT_COLOUR.label,
        help=f"Report colour palette (default: {ReportTheme.DEFAULT_COLOUR.label}).",
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
        "--image-watermark",
        type=Path,
        default=None,
        help="Path to an image file to use as a background watermark on report pages.",
    )
    parser.add_argument(
        "--extra-data",
        type=Path,
        default=None,
        help="Path to a JSON file containing extra report data (footnotes, label overrides, etc.).",
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


def doConversion(args: argparse.Namespace) -> tuple[ConversionResults, ExcelProcessor]:
    resultsBuilder = ConversionResultsBuilder(consoleOutput=True)
    with resultsBuilder.processingContext(
        "mireport Excel to validated Inline Report"
    ) as pc:
        pc.mark("Loading taxonomy metadata")
        mireport.loadBuiltInTaxonomyJSON()
        allTaxonomies = mireport.taxonomy.listTaxonomies()
        pc.addDevInfoMessage(
            f"Taxonomies entry points ({len(allTaxonomies)}) available: {', '.join(allTaxonomies)}"
        )
        pc.mark(
            "Extracting data from Excel",
            additionalInfo=f"Using file: {args.excel_file}",
        )
        excel = ExcelProcessor(
            args.excel_file,
            resultsBuilder,
            VSME_DEFAULTS,
            outputLocale=args.output_locale,
        )
        report = excel.populateReport()

        report.theme.setDisplayMode(args.style_mode).setColour(
            ColourPalette.from_label(args.style_palette)
        )

        for arg_name, setter in [
            ("image_logo", report.theme.setLogo),
            ("image_cover", report.theme.setCoverImage),
            ("image_watermark", report.theme.setWatermark),
        ]:
            if image_path := getattr(args, arg_name):
                image, err = ImageFileLikeAndFileName.prepare(image_path)
                if err:
                    pc.addDevInfoMessage(err)
                elif image:
                    setter(image)

        if (extra_file := args.extra_data) and extra_file.is_file():
            extra = json.loads(extra_file.read_text(encoding="utf-8"))

            for fn in extra.get("footnotes", []):
                report.addFootnote(
                    fn.get("content", ""),
                    group=fn.get("group"),
                    concept=fn.get("concept"),
                    concepts=fn.get("concepts"),
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
            for section in extra.get("optionalSections", []):
                section_id = section.get("id")
                setter = optional_section_setters.get(section_id)
                if setter is None:
                    pc.addDevInfoMessage(f"Unknown optionalSections id: {section_id!r}")
                    continue
                if "path" in section:
                    pc.mark(
                        "Converting Word document",
                        additionalInfo=f"Creating {section_id} from {section['path']}",
                    )
                    docx_path = _resolve_extra_path(extra_file, section["path"])
                    setter(_convert_docx_to_markup(docx_path, pc))
                elif "content" in section:
                    setter(Markup(section["content"]))
                else:
                    pc.addDevInfoMessage(
                        f"optionalSections entry {section_id!r} has neither 'path' nor 'content'"
                    )

            for rtv in extra.get("replacementTextblockValues", []):
                pc.mark(
                    "Converting Word document",
                    additionalInfo=f"Replacing {rtv['concept']} from {rtv['path']}",
                )
                docx_path = _resolve_extra_path(extra_file, rtv["path"])
                markup = _convert_docx_to_markup(docx_path, pc)
                report.replaceFactValue(rtv["concept"], markup)

        pc.mark("Generating Inline Report")
        reportFile = report.getInlineReport()
        reportPackage = report.getInlineReportPackage()

        output_path, dir_specified = prepare_output_path(args.output_path, args.force)
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

        if not args.skip_validation:
            pc.mark(
                "Validating using Arelle",
                additionalInfo=f"({ARELLE_VERSION_INFORMATION})",
            )
            pc.addDevInfoMessage(f"Using Inline Report package: {reportPackage}")
            arp = ArelleReportProcessor(
                taxonomyPackages=args.taxonomy_packages,
                workOffline=args.offline,
            )

            if not args.viewer and not args.json:
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
                else:
                    pc.addDevInfoMessage("Failed to create viewer.")

            if args.json:
                arelleResults = arp.generateXBRLJson(reportPackage)
                resultsBuilder.addMessages(arelleResults.messages)

                if arelleResults.has_json:
                    json_output = arelleResults.xBRL_JSON
                    if not dir_specified:
                        json_path = output_path.with_suffix(".json")
                        json_output.saveToFilepath(json_path)
                    else:
                        json_output.saveToDirectory(output_path)
                else:
                    pc.addDevInfoMessage("Failed to create JSON output.")

    return resultsBuilder.build(), excel


def outputMessages(
    args: argparse.Namespace, result: ConversionResults, excel: ExcelProcessor
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
        for message in messages:
            print(f"\t{message}")

    if args.devinfo and excel.unusedNames:
        max_output = 40
        unused = excel.unusedNames
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
    return


def main() -> None:
    parser = createArgParser()
    args = parseArgs(parser)
    result, excel = doConversion(args)
    outputMessages(args, result, excel)
    return


if __name__ == "__main__":
    rich.traceback.install(show_locals=False)
    logging.basicConfig(
        format="%(message)s",
        datefmt="[%Y-%m-%d %H:%M:%S]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )
    logging.captureWarnings(True)
    main()
