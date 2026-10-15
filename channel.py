"""Frame dictionaries, CRC32, and the unreliable channel for DATA and ACKs."""

import zlib

from simulation import log_event, schedule_event


def calculate_checksum(frame):
    """Checksum the frame type, sequence number, and payload together."""
    header = f"{frame['frame_type']}:{frame['sequence_number']}:".encode()
    return zlib.crc32(header + frame["payload"])


def create_frame(frame_type, sequence_number, payload=b""):
    """Build a DATA or ACK frame. No absolute packet index goes on the channel."""
    frame = {
        "frame_type": frame_type,            # DATA or ACK.
        "sequence_number": sequence_number,  # Wraps modulo 2^k.
        "payload": payload,                  # Empty bytes for ACKs.
        "checksum": 0,
    }
    frame["checksum"] = calculate_checksum(frame)
    return frame


def is_valid_frame(frame):
    """Check whether received contents still match the original checksum."""
    return frame["checksum"] == calculate_checksum(frame)


def sample_channel_conditions(state):
    """Make three independent random decisions for one transmission."""
    settings = state["settings"]
    random_generator = state["random_generator"]
    return {
        "lost": random_generator.random() < settings["loss"],
        "corrupted": random_generator.random() < settings["corruption"],
        "delayed": random_generator.random() < settings["delay"],
    }


def send_frame(state, frame, retransmission=False):
    """Count and log a send, then lose the frame or schedule its arrival."""
    frame_type = frame["frame_type"]
    sequence_number = frame["sequence_number"]
    statistics = state["statistics"]

    # Count attempts even if the channel subsequently loses the frame.
    if frame_type == "DATA":
        statistics["data_transmissions"] += 1
        if retransmission:
            statistics["retransmissions"] += 1
            log_event(state, "RETRANSMISSION", f"seq={sequence_number}")
        else:
            log_event(state, "DATA SEND", f"seq={sequence_number}")
        arrival_type = "data_arrival"
    else:
        statistics["ack_transmissions"] += 1
        log_event(state, "ACK SEND", f"seq={sequence_number}")
        arrival_type = "ack_arrival"

    conditions = sample_channel_conditions(state)
    if conditions["lost"]:
        log_event(state, "LOSS", f"{frame_type} seq={sequence_number}")
        return

    if conditions["corrupted"]:
        log_event(state, "CHANNEL CORRUPTION", f"{frame_type} seq={sequence_number}")
        # Change the transmitted copy, keeping the original checksum. XOR 1
        # flips the lowest sequence bit, so checksum validation will fail.
        frame = frame.copy()
        frame["sequence_number"] = sequence_number ^ 1

    travel_time = 1.0
    if conditions["delayed"]:
        travel_time += state["random_generator"].uniform(1.0, 3.0)
        log_event(
            state, "DELAY",
            f"{frame_type} seq={frame['sequence_number']}, sampled travel={travel_time:.3f}",
        )

    # FIFO in each direction: later DATA waits behind earlier DATA; ACKs have
    # their own queue. Every arrival still occurs within 4 units of its send.
    arrival_time = state["time"] + travel_time
    previous_arrival = state["channel"]["last_arrival"][frame_type]
    if arrival_time < previous_arrival:
        arrival_time = previous_arrival
    state["channel"]["last_arrival"][frame_type] = arrival_time

    event = {"type": arrival_type, "frame": frame}
    schedule_event(state, arrival_time - state["time"], event)
