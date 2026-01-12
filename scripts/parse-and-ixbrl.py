import argparse
import logging
from pathlib import Path

import rich.traceback
from rich.logging import RichHandler

import mireport
import mireport.taxonomy
from mireport.arelle.report_info import (
    ARELLE_VERSION_INFORMATION,
    ArelleReportProcessor,
)
from mireport.cli import validateTaxonomyPackages
from mireport.conversionresults import ConversionResults, ConversionResultsBuilder
from mireport.excelprocessor import (
    VSME_DEFAULTS,
    ExcelProcessor,
)
from mireport.localise import EU_LOCALES, argparse_locale


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
        mireport.loadTaxonomyJSON()
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
            if args.viewer:
                arelleResults = arp.generateInlineViewer(reportPackage)
                if arelleResults.has_viewer:
                    viewer = arelleResults.viewer
                    if not dir_specified:
                        viewer.saveToFilepath(output_path)
                    else:
                        viewer.saveToDirectory(output_path)
                else:
                    pc.addDevInfoMessage("Failed to create viewer.")
            else:
                arelleResults = arp.validateReportPackage(reportPackage)

            resultsBuilder.addMessages(arelleResults.messages)
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
        print(f"Information and issues encountered ({len(result)} messages):")
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
