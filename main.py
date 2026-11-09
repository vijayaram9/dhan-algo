import argparse
import sys
import warnings

# Suppress library deprecation notices
warnings.filterwarnings("ignore")

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from smart_edge_engine import smart_edge_engine
from config import config
from journal import journal

def parse_args():
    parser = argparse.ArgumentParser(description="NIFTY SMART EDGE v1.0 - Algorithmic Options Trading")
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default=config.trading_mode.lower(),
        help="Execution mode: 'paper' (simulation) or 'live' (real capital via Dhan)"
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=0,
        help="Number of ticks to run (0 for continuous execution)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3,
        help="Seconds between market ticks (default: 3s)"
    )
    parser.add_argument(
        "--stats", "--journal",
        action="store_true",
        help="Display historical trading journal analytics and experience log"
    )
    return parser.parse_args()

def main():
    args = parse_args()
    
    if args.stats:
        journal.print_performance_analytics()
        return

    mode = args.mode.upper()

    print("\n" + "=" * 65)
    print("      ⚡ NIFTY SMART EDGE v1.0 - MULTI-STRATEGY ENGINE ⚡      ")
    print("=" * 65)

    if mode == "LIVE":
        print("\n[!] CAUTION: LIVE BROKER TRADING REQUESTED!")
        print(f"Account Capital: Rs.25,000 | Max Daily Loss Ceiling: Rs.500 (2%) | Lot Size: 65")
        confirm = input("Type 'CONFIRM' to proceed with LIVE Dhan execution: ").strip()
        if confirm != "CONFIRM":
            print("[!] Live execution cancelled. Defaulting back to safe PAPER mode.")
            mode = "PAPER"

    engine = smart_edge_engine
    engine.mode = mode
    engine.initialize()

    iterations = sys.maxsize if args.ticks == 0 else args.ticks
    engine.run_loop(iterations=iterations, interval_seconds=args.interval)

if __name__ == "__main__":
    main()
