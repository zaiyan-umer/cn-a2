"""Shared state, scheduling, logging, and delivery; no protocol decisions here."""

import heapq
import math
import random

from results import create_statistics


def default_settings():
    """Return a fresh settings dictionary for one transfer."""
    return {
        "protocol": "gbn",
        "packets": 20,
        "window": 4,
        "bits": 3,
        "loss": 0.0,
        "corruption": 0.0,
        "delay": 0.0,
        "timeout": 10.0,
        "seed": None,
        "quiet": False,
    }


def validate_settings(settings):
    """Reject settings that cannot describe a valid, completing transfer."""
    protocol = settings["protocol"]
    if protocol not in ("gbn", "sr"):
        raise ValueError("protocol must be gbn or sr")

    for name in ("packets", "window", "bits"):
        value = settings[name]
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    timeout = settings["timeout"]
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive and finite")

    for name in ("loss", "corruption", "delay"):
        probability = settings[name]
        if not 0 <= probability <= 1:
            raise ValueError(f"{name} probability must be in [0, 1]")

    if settings["loss"] == 1 or settings["corruption"] == 1:
        raise ValueError("loss/corruption probability 1 prevents completion")

    sequence_space = 2 ** settings["bits"]
    if protocol == "gbn":
        maximum_window = sequence_space - 1
    else:
        maximum_window = sequence_space // 2

    if settings["window"] > maximum_window:
        raise ValueError(
            f"{protocol.upper()} window must be <= {maximum_window} "
            f"for k={settings['bits']}"
        )


def create_state(settings):
    """Create shared state; gbn/sr.initialize_protocol fills sender and receiver."""
    # Copy options so the run never changes the caller's settings dictionary.
    complete_settings = default_settings()
    if settings is not None:
        complete_settings.update(settings)
    validate_settings(complete_settings)

    original_payloads = []
    for packet_index in range(complete_settings["packets"]):
        original_payloads.append(f"Message {packet_index + 1}".encode())

    return {
        "settings": complete_settings,
        "time": 0.0,                       # Current simulated time.
        "events": [],                      # Min-heap of (time, order, event).
        "next_event_order": 0,             # Breaks equal-time ties in send order.
        "random_generator": random.Random(complete_settings["seed"]),
        "sequence_space": 2 ** complete_settings["bits"],
        "original_payloads": original_payloads,
        "delivered_payloads": [],          # Application data in delivery order.
        "sender": {},                      # Filled by the selected protocol.
        "receiver": {},
        "channel": {"last_arrival": {"DATA": 0.0, "ACK": 0.0}},
        "statistics": create_statistics(complete_settings["protocol"]),
    }


def schedule_event(state, delay, event):
    """Queue an event dictionary to be handled after a simulated delay.

    Arrival events contain type (data_arrival or ack_arrival) and frame.
    Timeout events contain type (timeout) and timer_version; SR also includes
    packet_index. These are local events, not fields transmitted in frames.
    """
    event_time = state["time"] + delay
    event_order = state["next_event_order"]
    state["next_event_order"] += 1
    heapq.heappush(state["events"], (event_time, event_order, event))


def log_event(state, event_name, detail):
    """Print one timestamped action unless this run is quiet."""
    if not state["settings"]["quiet"]:
        print(f"[{state['time']:9.3f}] {event_name:<22} {detail}")


def deliver_packet(state, payload, sequence_number):
    """Record application delivery, which is different from merely receiving DATA."""
    state["delivered_payloads"].append(payload)
    state["statistics"]["delivered"] += 1
    delivered_count = state["statistics"]["delivered"]
    log_event(state, "DELIVER", f"seq={sequence_number}, total={delivered_count}")
