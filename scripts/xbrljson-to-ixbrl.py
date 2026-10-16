"""Turn an xBRL-JSON report into a themed Inline XBRL report.

The taxonomy the report names must already be baked (scripts/update-taxonomy.py); pass its JSON
with --taxonomy-json. The report is rendered and, unless --skip-validation, validated with Arelle.
"""

import argparse
import logging
from pathlib import Path

import mireport
from mireport.arelle.report_info import (
    ARELLE_VERSION_INFORMATION,
    ArelleReportProcessor,
)
from mireport.cli import (
    configure_rich_output,
    console_print_plain,
    validateTaxonomyPackages,
)
from mireport.cli import (
    console_print as print,
)
from mireport.conversionresults import ConversionResults, ConversionResultsBuilder
from mireport.filesupport import ImageFileLikeAndFileName
from mireport.report.theme import ColourPalette, DisplayMode, ReportTheme
from mireport.taxonomy import loadTaxonomyJSON
from mireport.xbrljson_reader import XbrlJsonProcessor


def createArgParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read an xBRL-JSON report and generate a themed Inline XBRL report."
    )
    parser.add_argument("json_file", type=Path, help="Path to the xBRL-JSON report")
    parser.add_argument(
        "output_path",
        type=Path,
        help="Path to save the output: a file (with a suffix) or a directory.",
    )
    parser.add_argument(
        "--taxonomy-json",
        type=Path,
        nargs="+",
        required=True,
        help="Baked taxonomy JSON file(s); one must be for the report's entry point.",
    )
    parser.add_argument(
        "--entity-name-concept",
        default=None,
        help="Local name of the concept whose (string) fact is the entity's name.",
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Fail if any fact in the report cannot be added.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite quietly.")
    parser.add_argument(
        "--devinfo",
        action=argparse.BooleanOptionalAction,
        help="Show developer information messages too.",
    )
    parser.add_argument(
        "--taxonomy-packages",
        type=str,
        nargs="+",
        default=[],
        help="Taxonomy packages for Arelle (globs, *.zip, are permitted).",
    )
    parser.add_argument(
        "--offline",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Work offline (needs --taxonomy-packages).",
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
        help="Generate an ixbrl-viewer version as well.",
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
        help="Custom report accent colour as a 6-digit hex code.",
    )
    parser.add_argument("--image-logo", type=Path, default=None)
    parser.add_argument("--image-cover", type=Path, default=None)
    parser.add_argument("--image-background", type=Path, default=None)
    parser.add_argument(
        "--debug", action=argparse.BooleanOptionalAction, help="Debug logging."
    )
    return parser


def parseArgs(parser: argparse.ArgumentParser) -> argparse.Namespace:
    args = parser.parse_args()
    if args.offline and not args.taxonomy_packages:
        parser.error("--offline needs --taxonomy-packages")
    if args.taxonomy_packages:
        args.taxonomy_packages = validateTaxonomyPackages(
            args.taxonomy_packages, parser
        )
    if args.debug:
        logging.getLogger("mireport").setLevel(logging.DEBUG)
    return args


def doConversion(args: argparse.Namespace) -> ConversionResults:
    resultsBuilder = ConversionResultsBuilder(consoleOutput=True)
    with resultsBuilder.processingContext("mireport xBRL-JSON to Inline Report") as pc:
        pc.mark("Loading taxonomy metadata")
        mireport.loadBuiltInTaxonomyJSON()
        for path in args.taxonomy_json:
            loadTaxonomyJSON(path)

        pc.mark("Reading xBRL-JSON", additionalInfo=f"Using file: {args.json_file}")
        processor = XbrlJsonProcessor.from_file(
            args.json_file,
            resultsBuilder,
            strict=args.strict,
            entityNameConcept=args.entity_name_concept,
        )
        report = processor.createReport()
        pc.addDevInfoMessage(
            f"{processor.factsAdded} facts added, {processor.factsSkipped} skipped"
        )

        colour = ColourPalette.parse(args.style_custom or args.style_preset)
        report.theme.setDisplayMode(args.style_mode).setColour(colour)
        for arg_name, imageSetter in [
            ("image_logo", report.theme.setLogoImage),
            ("image_cover", report.theme.setCoverImage),
            ("image_background", report.theme.setBackgroundImage),
        ]:
            if image_path := getattr(args, arg_name):
                image, err = ImageFileLikeAndFileName.prepare(image_path)
                if err:
                    pc.addDevInfoMessage(err)
                elif image:
                    imageSetter(image)

        pc.mark("Generating Inline Report")
        reportFile = report.getInlineReport()
        reportPackage = report.getInlineReportPackage()

        output = args.output_path
        toDirectory = not output.suffix
        if output.exists() and not toDirectory and not args.force:
            print(f"Warning: overwriting existing file: {output}")
        (output if toDirectory else output.parent).mkdir(parents=True, exist_ok=True)
        if toDirectory:
            reportFile.saveToDirectory(output)
            reportPackage.saveToDirectory(output)
        else:
            reportFile.saveToFilepath(output)

        if not args.skip_validation:
            pc.mark(
                "Validating using Arelle",
                additionalInfo=f"({ARELLE_VERSION_INFORMATION})",
            )
            arp = ArelleReportProcessor(
                taxonomyPackages=args.taxonomy_packages, workOffline=args.offline
            )
            if args.viewer:
                arelleResults = arp.generateInlineViewer(reportPackage)
                resultsBuilder.addMessages(arelleResults.messages)
                if arelleResults.has_viewer:
                    if toDirectory:
                        arelleResults.viewer.saveToDirectory(output)
                    else:
                        arelleResults.viewer.saveToFilepath(
                            output.with_suffix(".viewer.html")
                        )
            else:
                arelleResults = arp.validateReportPackage(reportPackage)
                resultsBuilder.addMessages(arelleResults.messages)
    return resultsBuilder.build()


def outputMessages(args: argparse.Namespace, result: ConversionResults) -> None:
    messages = result.developerMessages if args.devinfo else result.userMessages
    if messages:
        print()
        print(f"Information and issues encountered ({len(messages)}):")
        console_print_plain(messages)


def main() -> None:
    args = parseArgs(createArgParser())
    outputMessages(args, doConversion(args))


if __name__ == "__main__":
    configure_rich_output()
    main()
