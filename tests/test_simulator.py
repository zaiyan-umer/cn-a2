"""Deterministic faults and regression checks for the procedural simulator."""

from contextlib import redirect_stdout
import heapq
import io
import unittest
from unittest.mock import patch

import channel
import gbn
import sr
from main import create_simulation, run_simulation, simulate


def no_fault():
    """Channel decisions for a reliable transmission."""
    return {"lost": False, "corrupted": False, "delayed": False}


def one_fault(frame_type, sequence_number, corrupt=False):
    """Return a test function that damages only the first matching transmission."""
    used = False

    def decide(frame):
        nonlocal used
        conditions = no_fault()
        if used:
            return conditions
        if frame["frame_type"] != frame_type:
            return conditions
        if frame["sequence_number"] != sequence_number:
            return conditions

        used = True
        if corrupt:
            conditions["corrupted"] = True
        else:
            conditions["lost"] = True
        return conditions

    return decide


class SimulatorTests(unittest.TestCase):
    def run_transfer(self, protocol, packets=12, fault=None, **options):
        """Record sends and replace randomness with the requested test fault."""
        settings = {"protocol": protocol, "packets": packets, "quiet": True}
        settings.update(options)
        state = create_simulation(settings)
        sends = []

        if protocol == "gbn":
            protocol_module = gbn
        else:
            protocol_module = sr

        def record_send(state, frame, retransmission=False):
            sends.append((state["time"], frame["frame_type"], frame["sequence_number"]))
            conditions = no_fault()
            if fault is not None:
                conditions = fault(frame)
            with patch("channel.sample_channel_conditions", return_value=conditions):
                channel.send_frame(state, frame, retransmission)

        with patch.object(protocol_module, "send_frame", side_effect=record_send):
            statistics = run_simulation(state)

        self.assertEqual(state["delivered_payloads"], state["original_payloads"])
        self.assertEqual(state["sender"]["base"], packets)
        self.assertEqual(statistics["delivered"], packets)
        self.assertEqual(
            statistics["data_transmissions"], packets + statistics["retransmissions"]
        )
        return state, sends

    def test_reliable_delivery_and_completion_clock(self):
        for protocol in ("gbn", "sr"):
            with self.subTest(protocol=protocol):
                state, sends = self.run_transfer(protocol)
                self.assertEqual(state["statistics"]["data_transmissions"], 12)
                self.assertEqual(state["statistics"]["retransmissions"], 0)
                self.assertEqual(state["statistics"]["completion_time"], 6.0)

    def test_gbn_retransmits_outstanding_window_and_discards(self):
        state, sends = self.run_transfer("gbn", packets=4, fault=one_fault("DATA", 0))
        retries = []
        early_acks = []
        for time, frame_type, sequence_number in sends:
            if frame_type == "DATA" and time >= 10:
                retries.append(sequence_number)
            if frame_type == "ACK" and time < 10:
                early_acks.append(sequence_number)

        self.assertEqual(retries, [0, 1, 2, 3])
        self.assertEqual(state["statistics"]["retransmissions"], 4)
        # All three out-of-order packets repeat the "nothing received" ACK.
        self.assertEqual(early_acks, [7, 7, 7])

    def test_sr_retransmits_only_missing_and_buffers(self):
        state, sends = self.run_transfer("sr", packets=4, fault=one_fault("DATA", 0))
        retries = []
        early_acks = []
        for time, frame_type, sequence_number in sends:
            if frame_type == "DATA" and time >= 10:
                retries.append(sequence_number)
            if frame_type == "ACK" and time < 10:
                early_acks.append(sequence_number)

        self.assertEqual(retries, [0])
        self.assertEqual(state["statistics"]["retransmissions"], 1)
        # ACKs 1/2/3 precede the missing packet, but final payload order is exact.
        self.assertEqual(early_acks, [1, 2, 3])

    def test_lost_or_corrupted_ack_recovers_without_duplicate_delivery(self):
        for protocol in ("gbn", "sr"):
            for corrupt in (False, True):
                with self.subTest(protocol=protocol, corrupt=corrupt):
                    fault = one_fault("ACK", 0, corrupt=corrupt)
                    state, sends = self.run_transfer(protocol, packets=1, fault=fault)
                    self.assertEqual(state["statistics"]["retransmissions"], 1)
                    self.assertEqual(state["statistics"]["ack_transmissions"], 2)

    def test_corrupted_data_recovers(self):
        for protocol in ("gbn", "sr"):
            with self.subTest(protocol=protocol):
                fault = one_fault("DATA", 0, corrupt=True)
                state, sends = self.run_transfer(protocol, packets=4, fault=fault)
                self.assertGreater(state["statistics"]["retransmissions"], 0)

    def test_wraps_at_smallest_and_assignment_sequence_spaces(self):
        for protocol in ("gbn", "sr"):
            for bits, window in ((1, 1), (3, 4)):
                with self.subTest(protocol=protocol, bits=bits):
                    state, sends = self.run_transfer(
                        protocol, packets=40, bits=bits, window=window
                    )
                    sequences = []
                    for time, frame_type, sequence_number in sends:
                        if frame_type == "DATA":
                            sequences.append(sequence_number)
                    expected = [index % (2 ** bits) for index in range(40)]
                    self.assertEqual(sequences, expected)

    def test_checksum_covers_data_ack_and_payload(self):
        for frame_type in ("DATA", "ACK"):
            payload = b""
            if frame_type == "DATA":
                payload = b"example"
            frame = channel.create_frame(frame_type, 3, payload)
            self.assertTrue(channel.is_valid_frame(frame))

            for field, changed_value in (
                ("sequence_number", 2), ("payload", b"changed"), ("frame_type", "OTHER")
            ):
                changed_frame = frame.copy()
                changed_frame[field] = changed_value
                self.assertFalse(channel.is_valid_frame(changed_frame))

    def test_channel_fifo_bounded_delay_and_independent_directions(self):
        state = create_simulation({"quiet": True})
        delayed = {"lost": False, "corrupted": False, "delayed": True}
        conditions = [delayed, no_fault(), no_fault()]

        # DATA 0 is delayed to time 4; DATA 1 waits behind it.
        # ACKs use a separate direction and can arrive at time 1.
        with patch("channel.sample_channel_conditions", side_effect=conditions):
            with patch.object(state["random_generator"], "uniform", return_value=3):
                channel.send_frame(state, channel.create_frame("DATA", 0))
                channel.send_frame(state, channel.create_frame("DATA", 1))
                channel.send_frame(state, channel.create_frame("ACK", 0))

        received = []
        while state["events"]:
            event_time, event_order, event = heapq.heappop(state["events"])
            frame = event["frame"]
            received.append((event_time, frame["frame_type"], frame["sequence_number"]))
        self.assertEqual(
            received, [(1.0, "ACK", 0), (4.0, "DATA", 0), (4.0, "DATA", 1)]
        )

    def test_six_required_experiments_and_reproducibility(self):
        # Recorded before the readability refactor. These are regression
        # expectations only; experiment output always comes from real runs.
        # Columns: protocol, loss, corruption, delay, DATA tx, retries, ACK tx, time.
        expected_runs = [
            ("gbn", 0, 0, 0, 200, 0, 200, 100.0),
            ("sr", 0, 0, 0, 200, 0, 200, 100.0),
            ("gbn", 0.10, 0, 0, 264, 64, 244, 274.0),
            ("sr", 0.10, 0, 0, 236, 36, 217, 376.0),
            ("gbn", 0.05, 0.05, 0.10, 260, 60, 255, 303.2037904059973),
            ("sr", 0.05, 0.05, 0.10, 243, 43, 217, 392.6152312224445),
        ]
        for expected_run in expected_runs:
            protocol, loss, corruption, delay, data_count, retries, ack_count, time = expected_run
            settings = {
                "protocol": protocol, "packets": 200, "window": 4, "bits": 3,
                "loss": loss, "corruption": corruption, "delay": delay,
                "seed": 42, "quiet": True,
            }
            with self.subTest(protocol=protocol, loss=loss, corruption=corruption):
                state = create_simulation(settings)
                statistics = run_simulation(state)
                self.assertEqual(state["delivered_payloads"], state["original_payloads"])
                self.assertEqual(statistics["delivered"], 200)
                self.assertEqual(statistics["data_transmissions"], data_count)
                self.assertEqual(statistics["retransmissions"], retries)
                self.assertEqual(statistics["ack_transmissions"], ack_count)
                self.assertEqual(statistics["completion_time"], time)
                self.assertEqual(data_count, 200 + retries)
                self.assertEqual(statistics, simulate(settings))

    def test_short_timeouts_with_delays_and_many_wraps(self):
        # Spurious retransmissions stress stale DATA, ACKs, and timer versions.
        for protocol in ("gbn", "sr"):
            settings = {
                "protocol": protocol, "packets": 80, "seed": 9, "quiet": True,
                "timeout": 2.5, "loss": 0.05, "corruption": 0.05, "delay": 0.7,
            }
            state = create_simulation(settings)
            run_simulation(state)
            self.assertEqual(state["delivered_payloads"], state["original_payloads"])
            self.assertGreater(state["statistics"]["retransmissions"], 0)

    def test_logs_include_protocol_and_channel_actions(self):
        output = io.StringIO()
        with redirect_stdout(output):
            simulate({"packets": 1, "seed": 42})
        for event in ("DATA SEND", "DATA RECEIVE", "ACK SEND", "ACK RECEIVE", "DELIVER"):
            self.assertIn(event, output.getvalue())

    def test_invalid_configuration(self):
        invalid_options = [
            {"window": 8}, {"protocol": "sr", "window": 5},
            {"packets": 0}, {"bits": 0}, {"window": 0},
            {"timeout": 0}, {"timeout": float("nan")},
            {"loss": -0.1}, {"loss": 1}, {"corruption": 1},
            {"delay": 1.1}, {"protocol": "tcp"},
        ]
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    create_simulation(options)

    def test_channel_corruption_does_not_mutate_original_frame(self):
        state = create_simulation({"quiet": True})
        for frame_type in ("DATA", "ACK"):
            with self.subTest(frame_type=frame_type):
                original = channel.create_frame(frame_type, 0)
                conditions = {"lost": False, "corrupted": True, "delayed": False}
                with patch("channel.sample_channel_conditions", return_value=conditions):
                    channel.send_frame(state, original)
                event_time, event_order, event = heapq.heappop(state["events"])
                self.assertTrue(channel.is_valid_frame(original))
                self.assertEqual(original["sequence_number"], 0)
                self.assertFalse(channel.is_valid_frame(event["frame"]))
                self.assertIsNot(original, event["frame"])

    def test_simulation_state_is_independent_between_runs(self):
        settings = {"protocol": "sr", "packets": 5, "quiet": True}
        original_settings = settings.copy()
        first = create_simulation(settings)
        second = create_simulation(settings)
        run_simulation(first)
        self.assertEqual(settings, original_settings)
        self.assertEqual(second["sender"]["base"], 0)
        self.assertEqual(second["sender"]["acknowledged_packets"], set())
        self.assertEqual(second["receiver"]["buffer"], {})
        self.assertEqual(second["delivered_payloads"], [])
        self.assertEqual(second["events"], [])
        self.assertEqual(second["statistics"]["data_transmissions"], 0)
        self.assertEqual(second["channel"]["last_arrival"], {"DATA": 0.0, "ACK": 0.0})
        self.assertEqual(run_simulation(second), first["statistics"])


if __name__ == "__main__":
    unittest.main()
