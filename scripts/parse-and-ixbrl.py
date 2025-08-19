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
from mireport.arelle.support import ArelleProcessingResult
from mireport.cli import validateTaxonomyPackages
from mireport.conversionresults import ConversionResults, ConversionResultsBuilder
from mireport.excelprocessor import (
    VSME_DEFAULTS,
    ExcelProcessor,
)


def createArgParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract facts from Excel and generate HTML."
    )
    parser.add_argument("excel_file", type=Path, help="Path to the Excel file")
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory to save the generated HTML file to. Overwrites existing files.",
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


def doConversion(args: argparse.Namespace) -> tuple[ConversionResults, ExcelProcessor]:
    resultsBuilder = ConversionResultsBuilder(consoleOutput=True)
    with resultsBuilder.processingContext(
        "mireport Excel to validated Inline Report"
    ) as pc:
        pc.mark("Loading taxonomy metadata")
        mireport.loadMetaData()
        pc.addDevInfoMessage(
            f"Taxonomies available: {', '.join(mireport.taxonomy.listTaxonomies())}"
        )
        pc.mark(
            "Extracting data from Excel",
            additionalInfo=f"Using file: {args.excel_file}",
        )
        excel = ExcelProcessor(args.excel_file, resultsBuilder, VSME_DEFAULTS)
        report = excel.populateReport()
        pc.mark(
            "Generating Inline Report",
            additionalInfo=f"Writing files to {args.output_dir} ({report.factCount} facts to include)",
        )
        reportFile = report.getInlineReport()
        reportPackage = report.getInlineReportPackage()

        reportFile.saveToDirectory(args.output_dir)
        reportPackage.saveToDirectory(args.output_dir)

        if not args.skip_validation:
            pc.mark(
                "Validating using Arelle",
                additionalInfo=f"({ARELLE_VERSION_INFORMATION})",
            )
            pc.addDevInfoMessage(f"Created Inline Report package: {reportPackage}")
            arelleResults: ArelleProcessingResult = ArelleReportProcessor(
                taxonomyPackages=args.taxonomy_packages,
                workOffline=args.offline,
            ).validateReportPackage(
                reportPackage,
            )
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
    rich.traceback.install(show_locals=True)
    logging.basicConfig(
        format="%(message)s",
        datefmt="[%Y-%m-%d %H:%M:%S]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )
    logging.captureWarnings(True)
    main()
