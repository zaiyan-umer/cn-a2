"""Selective Repeat: individual ACKs, per-packet timers, and receiver buffering.

Sender/receiver positions are local absolute indices; frames carry only modulo
sequence numbers. The receive window is at most half the sequence space.
"""

from channel import create_frame, is_valid_frame, send_frame
from simulation import deliver_packet, log_event, schedule_event


def initialize_protocol(state):
    """Set up separate SR sender and receiver windows."""
    state["sender"] = {
        "base": 0,                       # First packet not consecutively ACKed.
        "next_packet_index": 0,          # Next original packet not yet sent.
        "acknowledged_packets": set(),   # ACKed indices waiting for base to advance.
        "packet_timer_versions": {},     # Outstanding index -> latest timer version.
    }
    state["receiver"] = {
        "base": 0,                       # Next absolute position to deliver.
        "buffer": {},                    # Receive position -> opaque payload.
    }


def send_available_packets(state):
    """Send new packets that fit inside the current sender window."""
    sender = state["sender"]
    window_end = sender["base"] + state["settings"]["window"]
    packet_limit = min(window_end, len(state["original_payloads"]))

    while sender["next_packet_index"] < packet_limit:
        packet_index = sender["next_packet_index"]
        sender["next_packet_index"] += 1
        send_packet(state, packet_index)


def send_packet(state, packet_index, retransmission=False):
    """Send one packet and start or replace only that packet's logical timer."""
    sequence_number = packet_index % state["sequence_space"]
    payload = state["original_payloads"][packet_index]
    frame = create_frame("DATA", sequence_number, payload)
    send_frame(state, frame, retransmission)

    timer_versions = state["sender"]["packet_timer_versions"]
    timer_version = timer_versions.get(packet_index, 0) + 1
    timer_versions[packet_index] = timer_version
    event = {
        "type": "timeout",
        "packet_index": packet_index,
        "timer_version": timer_version,
    }
    schedule_event(state, state["settings"]["timeout"], event)


def handle_timeout(state, event):
    """Repeat the timed-out packet only if it is still unacknowledged."""
    sender = state["sender"]
    packet_index = event["packet_index"]
    current_version = sender["packet_timer_versions"].get(packet_index)

    # ACKs remove timers and retransmissions replace their versions. Either
    # makes an older timeout event harmless when it eventually leaves the heap.
    if event["timer_version"] != current_version:
        return
    if packet_index in sender["acknowledged_packets"]:
        return
    if packet_index < sender["base"]:
        return

    sequence_number = packet_index % state["sequence_space"]
    log_event(state, "TIMEOUT", f"DATA seq={sequence_number}")
    send_packet(state, packet_index, retransmission=True)


def receive_ack(state, frame):
    """Mark one packet ACKed, then slide past consecutive ACKed packets."""
    sequence_number = frame["sequence_number"]
    log_event(state, "ACK RECEIVE", f"seq={sequence_number}")
    if not is_valid_frame(frame):
        log_event(state, "CORRUPTION DETECTED", "ACK ignored")
        return

    sender = state["sender"]
    sequence_space = state["sequence_space"]
    base_sequence = sender["base"] % sequence_space
    ack_offset = (sequence_number - base_sequence) % sequence_space
    packet_index = sender["base"] + ack_offset

    # Example: base=6 and ACK 0 in a space of 8 maps to absolute index 8.
    # Unlike GBN, this ACK covers index 8 alone, not indices 6 and 7.
    if packet_index >= sender["next_packet_index"]:
        log_event(state, "ACK IGNORE", f"duplicate/stale seq={sequence_number}")
        return
    if packet_index in sender["acknowledged_packets"]:
        log_event(state, "ACK IGNORE", f"duplicate/stale seq={sequence_number}")
        return

    sender["acknowledged_packets"].add(packet_index)
    sender["packet_timer_versions"].pop(packet_index, None)

    while sender["base"] in sender["acknowledged_packets"]:
        sender["acknowledged_packets"].remove(sender["base"])
        sender["base"] += 1
    send_available_packets(state)


def receive_data(state, frame):
    """Buffer current-window DATA and re-ACK duplicates from the previous window."""
    sequence_number = frame["sequence_number"]
    log_event(state, "DATA RECEIVE", f"seq={sequence_number}")
    if not is_valid_frame(frame):
        log_event(state, "CORRUPTION DETECTED", "DATA discarded")
        return

    receiver = state["receiver"]
    sequence_space = state["sequence_space"]
    window_size = state["settings"]["window"]
    base_sequence = receiver["base"] % sequence_space
    forward_distance = (sequence_number - base_sequence) % sequence_space
    backward_distance = (base_sequence - sequence_number) % sequence_space

    if forward_distance < window_size:
        packet_index = receiver["base"] + forward_distance
        if packet_index not in receiver["buffer"]:
            receiver["buffer"][packet_index] = frame["payload"]
            log_event(state, "BUFFER", f"seq={sequence_number}, expected={base_sequence}")
        else:
            log_event(state, "DUPLICATE", f"buffered seq={sequence_number}")

        acknowledgment = create_frame("ACK", sequence_number)
        send_frame(state, acknowledgment)
        deliver_buffered_packets(state)

    elif 1 <= backward_distance <= window_size:
        # A previously delivered packet can return because its ACK was lost.
        # ACK it again, but never deliver the same application data twice.
        log_event(state, "DUPLICATE", f"seq={sequence_number}; re-ACK")
        acknowledgment = create_frame("ACK", sequence_number)
        send_frame(state, acknowledgment)
    else:
        log_event(state, "DISCARD", f"seq={sequence_number}; outside receive window")


def deliver_buffered_packets(state):
    """Deliver from the receiver's base until the next missing position."""
    receiver = state["receiver"]
    while receiver["base"] in receiver["buffer"]:
        payload = receiver["buffer"].pop(receiver["base"])
        sequence_number = receiver["base"] % state["sequence_space"]
        deliver_packet(state, payload, sequence_number)
        receiver["base"] += 1
