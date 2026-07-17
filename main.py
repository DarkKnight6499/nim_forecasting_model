"""
Net Interest Margin (NIM) forecasting model - CLI entry point.
See README.md for usage examples and a full description of every report.
"""

import argparse

import config
import pipeline
from reporting import charts, console, dashboard, export


def build_arg_parser():
    parser = argparse.ArgumentParser(description="NIM forecasting model")
    parser.add_argument("--bank-name", type=str, help="Search FDIC BankFind for a bank by name and exit")
    parser.add_argument("--bank-cert", type=int, help="FDIC certificate number to calibrate the balance sheet to")
    parser.add_argument("--fred-api-key", type=str, help="FRED API key (or set FRED_API_KEY env var)")
    parser.add_argument("--months", type=int, default=config.HORIZON_MONTHS, help="Forecast horizon in months")
    parser.add_argument("--output-dir", type=str, default="outputs", help="Directory for charts/Excel output")
    parser.add_argument("--ftp-recalibrate", action="store_true", help="Run the FTP policy spread optimizer")
    parser.add_argument("--deposit-history", type=str, help="CSV (month, product, balance) to estimate NMD decay from")
    parser.add_argument("--backtest", type=str, help="Actuals CSV to back-test the base scenario forecast against")
    parser.add_argument("--backtest-fdic", type=int, nargs="?", const=8, default=None, metavar="N",
                         help="Real out-of-sample back-test (requires --bank-cert), N quarters back, default 8")
    return parser


def main():
    args = build_arg_parser().parse_args()

    if args.bank_name:
        console.print_bank_search(args.bank_name, pipeline.search_bank(args.bank_name))
        return

    results = pipeline.run(
        bank_cert=args.bank_cert, fred_api_key=args.fred_api_key, months=args.months,
        output_dir=args.output_dir, ftp_recalibrate=args.ftp_recalibrate,
        deposit_history=args.deposit_history, backtest=args.backtest, backtest_fdic=args.backtest_fdic,
    )

    console.print_report(results)
    charts.generate_all(results)
    export.export_from_results(results)
    dashboard.write_dashboard(results, results.output_dir / "dashboard.html")
    console.print_output_paths(results)


if __name__ == "__main__":
    main()
