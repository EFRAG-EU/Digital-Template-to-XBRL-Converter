import argparse
import logging
import time
from pathlib import Path

from mireport.arelle.report_info import ArelleReportProcessor, getOrCreateReportPackage
from mireport.cli import (
    configure_rich_output,
    console_print_plain,
    validateTaxonomyPackages,
)
from mireport.cli import console_print as print
from mireport.conversionresults import (
    ConversionResults,
    ConversionResultsBuilder,
    Severity,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check an XBRL report is valid and, optionally, and create a viewer for it including any validation messages."
    )
    parser.add_argument(
        "report_path",
        type=Path,
        help="Path to the report (bare XHTML file or XBRL report package) to be checked.",
    )
    parser.add_argument(
        "--taxonomy-packages",
        type=str,
        nargs="+",
        default=[],
        help="Paths to the taxonomy packages to be used (globs such as *.zip, and directories of zips, are permitted).",
    )
    parser.add_argument(
        "--viewer-path",
        type=Path,
        default=None,
        help="The path of the viewer to be created.",
    )
    parser.add_argument(
        "--json-path",
        type=Path,
        default=None,
        help="The path of the xBRL-JSON to be created.",
    )
    parser.add_argument(
        "--ignore-calculation-warnings",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Ignore calculation warnings when validating the XBRL report.",
    )
    verbosity_control = parser.add_mutually_exclusive_group()
    verbosity_control.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output.",
    )
    verbosity_control.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Quiet output (overrides verbose if both specified).",
    )
    parser.add_argument(
        "--devinfo",
        action=argparse.BooleanOptionalAction,
        help="Enable display of developer information issues (not normally visible to users)",
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        help="Turn on debugging output.",
    )
    args = parser.parse_args()
    if args.taxonomy_packages:
        args.taxonomy_packages = validateTaxonomyPackages(
            args.taxonomy_packages, parser
        )
    return args


def main() -> None:
    start = time.perf_counter_ns()

    args = parse_args()
    if args.debug:
        print("[red]Debugging information will be included in the output.[/red]")
        logging.root.setLevel(logging.DEBUG)

    report_path: Path = args.report_path
    taxonomy_packages: list[Path] = args.taxonomy_packages
    viewer_path: Path | None = args.viewer_path
    json_path: Path | None = args.json_path

    if taxonomy_packages:
        workOffline = True
        print("Taxonomy packages specified so working OFFLINE.")
    else:
        print("No taxonomy packages specified so working ONLINE.")
        workOffline = False

    if not report_path.is_file():
        raise SystemExit(f"Report path {report_path} cannot be found.")

    start = time.perf_counter_ns()
    print("Calling into Arelle")
    arp = ArelleReportProcessor(
        taxonomyPackages=taxonomy_packages, workOffline=workOffline
    )
    source = getOrCreateReportPackage(report_path)

    outputsWanted: bool = bool(viewer_path or json_path)
    arelle_messages = []
    if not outputsWanted:
        arelle_result = arp.validateReportPackage(
            source, disableCalculationValidation=args.ignore_calculation_warnings
        )
        arelle_messages.extend(arelle_result.messages)
    else:
        if viewer_path:
            arelle_result = arp.generateInlineViewer(source)
            arelle_messages.extend(arelle_result.messages)
            if arelle_result.has_viewer:
                if viewer_path.is_file():
                    print(f"Overwriting {viewer_path}.")
                arelle_result.viewer.saveToFilepath(viewer_path)
                print(f"Viewer written to {viewer_path}.")
            else:
                print("Failed to create inline viewer.")

        if json_path:
            arelle_result = arp.generateXBRLJson(source)
            arelle_messages.extend(arelle_result.messages)
            if arelle_result.has_json:
                if json_path.is_file():
                    print(f"Overwriting {json_path}.")
                arelle_result.xbrl_json.saveToFilepath(json_path)
                print(f"xBRL-JSON written to {json_path}.")
            else:
                print("Failed to create xBRL-JSON.")
    results = ConversionResultsBuilder().addMessages(arelle_messages).build()
    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    print(f"Finished querying Arelle ({elapsed:,.2f} seconds elapsed).")

    if (args.verbose and results.userMessages) or results.hasErrorsOrWarnings():
        print()
        if results.hasErrors():
            print("The report has errors:")
        elif results.hasWarnings():
            print("The report has warnings:")
        else:
            print("Messages:")

        console_print_plain(results.userMessages)

    if args.devinfo and results.developerMessages:
        print()
        print("All messages (including developer messages):")
        console_print_plain(results.developerMessages)

    final_word_and_exit(results, args.quiet)


def final_word_and_exit(results: ConversionResults, quiet: bool) -> None:
    print()
    match results.getOverallSeverity():
        case Severity.ERROR:
            exitCode = 1
            if not quiet:
                print(
                    "[bold red]➡️ The XBRL report is INVALID (has errors). Please check the output above.❌"
                )
        case Severity.WARNING:
            exitCode = 0
            if not quiet:
                print(
                    "[bold dark_orange]➡️ The XBRL report is VALID but there are WARNINGS.⚠️"
                )
        case Severity.INFO:
            exitCode = 0
            if not quiet:
                print(
                    "[bold green]➡️ The XBRL report is VALID and has no errors or warnings.✅"
                )
    print()
    raise SystemExit(exitCode)


if __name__ == "__main__":
    configure_rich_output(locals_max_length=4)
    main()
