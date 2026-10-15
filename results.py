"""Plain statistics dictionaries and the six assignment experiments."""

import csv
from pathlib import Path


def create_statistics(protocol):
    """Start counters for one run; DATA and ACK attempts are kept separate."""
    return {
        "protocol": protocol,
        "scenario": "single",
        "data_transmissions": 0,  # Includes retransmissions and lost attempts.
        "retransmissions": 0,     # Repeated DATA packets, not timeout events.
        "delivered": 0,           # Original packets delivered to the application.
        "ack_transmissions": 0,
        "completion_time": 0.0,   # Time when the final original packet is ACKed.
    }


def print_table(rows):
    """Print one statistics row per transfer."""
    print(
        f"{'Scenario':<18} {'Protocol':<8} {'DATA tx':>8} {'Retrans':>8} "
        f"{'Delivered':>10} {'ACK tx':>8} {'Time':>10}"
    )
    for row in rows:
        print(
            f"{row['scenario']:<18} {row['protocol'].upper():<8} "
            f"{row['data_transmissions']:>8} {row['retransmissions']:>8} "
            f"{row['delivered']:>10} {row['ack_transmissions']:>8} "
            f"{row['completion_time']:>10.3f}"
        )


def explain_results(rows):
    """Describe the actual results in the experiment order: A, B, C; GBN, SR."""
    reliable_gbn, reliable_sr, loss_gbn, loss_sr, mixed_gbn, mixed_sr = rows
    return (
        f"On the reliable channel, GBN and SR required {reliable_gbn['data_transmissions']} and "
        f"{reliable_sr['data_transmissions']} DATA transmissions, with "
        f"{reliable_gbn['retransmissions']} and {reliable_sr['retransmissions']} "
        "retransmissions respectively. "
        f"With 10% loss, GBN retransmitted {loss_gbn['retransmissions']} packets and "
        f"SR retransmitted {loss_sr['retransmissions']}; under mixed errors the counts "
        f"were {mixed_gbn['retransmissions']} and {mixed_sr['retransmissions']}. "
        "GBN repeats its entire outstanding window after a timeout, whereas SR buffers "
        "out-of-order DATA and retries only unacknowledged packets. "
        f"SR finished later in these samples (loss: {loss_sr['completion_time']:.3f} "
        f"versus GBN's {loss_gbn['completion_time']:.3f} time units; mixed: "
        f"{mixed_sr['completion_time']:.3f} versus {mixed_gbn['completion_time']:.3f}), "
        "illustrating that fewer retransmissions do not guarantee shorter completion "
        "time when individual ACK recovery and different random fault histories affect progress."
    )


def save_csv(rows, destination):
    """Export the same columns shown by the original simulator."""
    with destination.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def save_graph(rows, destination, seed):
    """Compare GBN/SR retransmission counts across the three scenarios."""
    import matplotlib.pyplot as plt

    scenario_names = []
    gbn_retransmissions = []
    sr_retransmissions = []
    for row in rows:
        if row["protocol"] == "gbn":
            scenario_names.append(row["scenario"])
            gbn_retransmissions.append(row["retransmissions"])
        else:
            sr_retransmissions.append(row["retransmissions"])

    positions = list(range(len(scenario_names)))
    gbn_positions = [position - 0.18 for position in positions]
    sr_positions = [position + 0.18 for position in positions]

    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar(gbn_positions, gbn_retransmissions, width=0.36, label="GBN", color="#2864a0")
    axis.bar(sr_positions, sr_retransmissions, width=0.36, label="SR", color="#da8737")
    axis.set_xticks(positions, scenario_names)
    axis.set_ylabel("DATA retransmissions")
    axis.set_title(f"GBN vs SR — 200 packets, N=4, k=3, seed={seed}")
    axis.legend()
    axis.set_axisbelow(True)
    axis.yaxis.grid(True, alpha=0.25)
    figure.tight_layout()
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def run_experiments(simulate, output_dir, seed=42):
    """Run the required scenarios and write a table, CSV, graph, and explanation."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # Write PNGs without requiring a desktop window.
    except ImportError as error:
        raise ImportError("Experiments need Matplotlib; install requirements.txt first") from error

    # Each tuple is (scenario name, loss, corruption, additional-delay probability).
    scenarios = [
        ("A: Reliable", 0.0, 0.0, 0.0),
        ("B: Packet loss", 0.10, 0.0, 0.0),
        ("C: Mixed errors", 0.05, 0.05, 0.10),
    ]
    rows = []
    for name, loss, corruption, delay in scenarios:
        for protocol in ("gbn", "sr"):
            settings = {
                "protocol": protocol,
                "packets": 200,
                "window": 4,
                "bits": 3,
                "loss": loss,
                "corruption": corruption,
                "delay": delay,
                "timeout": 10.0,
                "seed": seed,
                "quiet": True,
            }
            row = simulate(settings)
            row["scenario"] = name
            if row["delivered"] != 200:
                raise RuntimeError("experiment did not deliver all 200 packets")
            if row["data_transmissions"] != 200 + row["retransmissions"]:
                raise RuntimeError("experiment DATA transmission counts are inconsistent")
            rows.append(row)

    print_table(rows)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    save_csv(rows, output / "results.csv")
    save_graph(rows, output / "retransmissions.png", seed)

    explanation = explain_results(rows)
    (output / "explanation.txt").write_text(explanation + "\n")
    print("\n" + explanation)
    print(f"\nSaved CSV, graph, and explanation to {output}/")
    return rows
