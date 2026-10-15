"""Command-line entry point and the explicit GBN/SR event loop."""

import argparse
import heapq

import gbn
import sr
from results import print_table, run_experiments
from simulation import create_state


def create_simulation(settings=None):
    """Prepare shared state and the selected protocol's sender and receiver."""
    state = create_state(settings)
    if state["settings"]["protocol"] == "gbn":
        gbn.initialize_protocol(state)
    else:
        sr.initialize_protocol(state)
    return state


def run_simulation(state):
    """Send the first window, then process arrivals and timeouts until done."""
    if state["settings"]["protocol"] == "gbn":
        protocol = gbn
    else:
        protocol = sr

    protocol.send_available_packets(state)
    packet_count = state["settings"]["packets"]

    # base is the first packet not yet consecutively acknowledged. When it
    # reaches packet_count, every original packet has been acknowledged.
    while state["sender"]["base"] < packet_count:
        if not state["events"]:
            raise RuntimeError("event queue exhausted before completion")

        event_time, event_order, event = heapq.heappop(state["events"])
        state["time"] = event_time

        if event["type"] == "data_arrival":
            protocol.receive_data(state, event["frame"])
        elif event["type"] == "ack_arrival":
            protocol.receive_ack(state, event["frame"])
        elif event["type"] == "timeout":
            protocol.handle_timeout(state, event)
        else:
            raise RuntimeError(f"unknown simulation event: {event['type']}")

    if state["delivered_payloads"] != state["original_payloads"]:
        raise RuntimeError("receiver did not deliver the original payloads exactly in order")

    # Stop here: obsolete queued timers do not extend the completion time.
    state["statistics"]["completion_time"] = state["time"]
    return state["statistics"]


def simulate(settings):
    """Run a transfer from a settings dictionary; return its statistics dictionary."""
    state = create_simulation(settings)
    return run_simulation(state)


def main():
    """Read command-line options and run a transfer or the six experiments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=("gbn", "sr"), default="gbn")
    parser.add_argument("--packets", type=int, default=20)
    parser.add_argument("--window", type=int, default=4)
    parser.add_argument("--bits", type=int, default=3, help="sequence-number bits k")
    parser.add_argument("--loss", type=float, default=0.0)
    parser.add_argument("--corruption", type=float, default=0.0)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--experiments", action="store_true", help="run the six fixed assignment scenarios"
    )
    parser.add_argument(
        "--output-dir", default="results", help="experiment CSV, PNG, and explanation directory"
    )
    args = parser.parse_args()

    try:
        if args.experiments:
            seed = args.seed
            if seed is None:
                seed = 42
            run_experiments(simulate, args.output_dir, seed=seed)
        else:
            settings = {
                "protocol": args.protocol,
                "packets": args.packets,
                "window": args.window,
                "bits": args.bits,
                "loss": args.loss,
                "corruption": args.corruption,
                "delay": args.delay,
                "timeout": args.timeout,
                "seed": args.seed,
                "quiet": args.quiet,
            }
            statistics = simulate(settings)
            print_table([statistics])
    except (ValueError, ImportError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
