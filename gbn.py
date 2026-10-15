"""Go-Back-N: cumulative ACKs, one timer, and no receiver buffering.

Sender indices are absolute local positions. Only their modulo sequence numbers
are transmitted. ACK seq means the last correctly received in-order packet.
"""

from channel import create_frame, is_valid_frame, send_frame
from simulation import deliver_packet, log_event, schedule_event


def initialize_protocol(state):
    """Set up the GBN sender and receiver before the first send."""
    state["sender"] = {
        "base": 0,                    # Oldest packet still waiting for an ACK.
        "next_packet_index": 0,       # Next original packet not yet sent.
        "timer_version": 0,           # Identifies the current oldest-packet timer.
    }
    state["receiver"] = {
        "next_expected_index": 0,     # Only this packet can be delivered next.
    }


def send_available_packets(state):
    """Send new packets until the sender window is full or all are sent."""
    sender = state["sender"]
    window_end = sender["base"] + state["settings"]["window"]
    packet_limit = min(window_end, len(state["original_payloads"]))

    while sender["next_packet_index"] < packet_limit:
        packet_index = sender["next_packet_index"]
        sender["next_packet_index"] += 1
        send_packet(state, packet_index)

        # The first outstanding packet needs a timer; later packets share it.
        if packet_index == sender["base"]:
            start_timer(state)


def send_packet(state, packet_index, retransmission=False):
    """Transmit one original payload using its wrapping sequence number."""
    sequence_number = packet_index % state["sequence_space"]
    payload = state["original_payloads"][packet_index]
    frame = create_frame("DATA", sequence_number, payload)
    send_frame(state, frame, retransmission)


def start_timer(state):
    """Replace the logical timer for the oldest unacknowledged packet."""
    sender = state["sender"]
    sender["timer_version"] += 1
    event = {"type": "timeout", "timer_version": sender["timer_version"]}
    schedule_event(state, state["settings"]["timeout"], event)


def handle_timeout(state, event):
    """If the active timer expires, repeat every outstanding packet."""
    sender = state["sender"]

    # Old timeout events remain in the heap after an ACK restarts the timer.
    # Their version no longer matches, so they must not cause retransmissions.
    if event["timer_version"] != sender["timer_version"]:
        return
    if sender["base"] == sender["next_packet_index"]:
        return

    sequence_number = sender["base"] % state["sequence_space"]
    log_event(state, "TIMEOUT", f"DATA seq={sequence_number}; retransmit outstanding window")
    for packet_index in range(sender["base"], sender["next_packet_index"]):
        send_packet(state, packet_index, retransmission=True)
    start_timer(state)


def receive_ack(state, frame):
    """Advance past all packets covered by a valid cumulative ACK."""
    sequence_number = frame["sequence_number"]
    log_event(state, "ACK RECEIVE", f"seq={sequence_number}")
    if not is_valid_frame(frame):
        log_event(state, "CORRUPTION DETECTED", "ACK ignored")
        return

    sender = state["sender"]
    sequence_space = state["sequence_space"]
    base_sequence = sender["base"] % sequence_space
    ack_offset = (sequence_number - base_sequence) % sequence_space
    outstanding_count = sender["next_packet_index"] - sender["base"]

    # Example: with 8 sequence numbers and base=6, ACK 0 is two positions
    # ahead: (0 - 6) % 8 = 2. It acknowledges absolute packets 6, 7, and 8.
    # Only an ACK that maps into the outstanding window can advance base.
    if ack_offset >= outstanding_count:
        log_event(state, "ACK IGNORE", f"duplicate/stale seq={sequence_number}")
        return

    sender["base"] += ack_offset + 1
    sender["timer_version"] += 1  # Invalidate the previous timer, even if now empty.
    if sender["base"] < sender["next_packet_index"]:
        start_timer(state)
    send_available_packets(state)


def receive_data(state, frame):
    """Deliver only the expected packet, then send the latest cumulative ACK."""
    sequence_number = frame["sequence_number"]
    log_event(state, "DATA RECEIVE", f"seq={sequence_number}")
    receiver = state["receiver"]
    expected_sequence = receiver["next_expected_index"] % state["sequence_space"]

    if not is_valid_frame(frame):
        log_event(state, "CORRUPTION DETECTED", "DATA discarded")
    elif sequence_number == expected_sequence:
        deliver_packet(state, frame["payload"], sequence_number)
        receiver["next_expected_index"] += 1
    else:
        log_event(state, "DISCARD", f"seq={sequence_number}; expected={expected_sequence}")

    # Repeating the last ACK helps recover from gaps and lost ACKs. Before any
    # delivery, (-1) % sequence_space means "nothing received yet" (7 for k=3).
    last_delivered_sequence = (receiver["next_expected_index"] - 1) % state["sequence_space"]
    acknowledgment = create_frame("ACK", last_delivered_sequence)
    send_frame(state, acknowledgment)
