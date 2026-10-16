# Go-Back-N and Selective Repeat Simulator

A small Computer Networks assignment simulator. Sender, receiver, channel, and
timers run in one Python program using a simulated clock and a `heapq` event
queue. There are no sockets, threads, or real-time waits.

The application uses ordinary functions and dictionaries. There are no application
classes, inheritance, or queued callback functions. Every function that changes a
run receives its `state` dictionary explicitly.

## Run

Python 3.10 or newer is required. Individual transfers use only the standard
library. Matplotlib is needed for the experiment graph.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# Small reliable transfer with detailed logs
python main.py --protocol gbn --packets 12 --seed 42

# SR with DATA and ACK loss, corruption, and additional delay
python main.py --protocol sr --packets 200 --window 4 --bits 3 \
  --loss 0.05 --corruption 0.05 --delay 0.10 --timeout 10 --seed 42

# Six assignment experiments, quiet transfers, consolidated results
python main.py --experiments --seed 42
```

`--quiet` suppresses detailed transfer logs but keeps the final statistics table.
`python main.py --help` lists every option. The default single transfer uses GBN,
20 packets, window 4, 3 sequence bits, no errors, and timeout 10. Without a seed,
single transfers use fresh random initialization; experiments default to seed 42.

## Protocol behavior

- **GBN:** Cumulative ACKs identify the last correctly received in-order sequence
  number. The receiver discards other packets and repeats its latest ACK. One
  active logical timer covers the oldest unacknowledged packet; its expiry
  retransmits the entire outstanding window.
- **SR:** Each packet has its own logical timer and individual ACK. The receiver
  buffers packets in its window and delivers consecutive payloads in order.
  Packets from the previous receiver window are acknowledged again, recovering
  from lost ACKs without delivering duplicate data.

Absolute indices exist only in local sender/receiver state. Frames contain kind,
modulo sequence number, opaque payload, and CRC32; ACKs have an empty payload.
Although example message text is numbered for readability, neither protocol uses
payload contents to decide which packet or ACK it has received. At completion,
the simulator checks the complete delivered payload list against the original
list, detecting missing, reordered, or duplicate application data.

With `k` sequence bits, GBN requires `1 <= N <= 2^k - 1` and SR requires
`1 <= N <= 2^(k-1)`. Both transfer arbitrarily more packets than the sequence
space. Timer versions make obsolete queued timeout events harmless.

## Channel and timing assumptions

For each DATA or ACK transmission, the channel independently samples loss,
corruption, and additional-delay decisions using the configured probabilities.
A lost frame never arrives. Corruption flips a sequence bit while retaining the
original CRC32, so checksum validation rejects the changed frame. Corrupted
ACKs are ignored; corrupted GBN DATA causes a repeat cumulative ACK, while
corrupted SR DATA is discarded without acknowledgment.

Normal one-way travel takes 1 simulated time unit. A delayed frame receives an
additional uniform delay of 1–3 units. Delivery is FIFO separately in each
direction: a later surviving DATA frame cannot overtake earlier DATA, and the
same rule applies independently to ACKs. Later frames can consequently wait
behind a delayed frame; every frame still arrives within 4 units of its send.
Simultaneous events execute in insertion order. At an exact timer/arrival tie,
this rule can cause a harmless extra retransmission.

FIFO delivery prevents stale copies from overtaking newer traffic after
sequence-number reuse. This is an intentional simplification: additional delay
does not reorder surviving frames. Loss still produces receiver gaps, allowing
GBN discarding and SR buffering to be demonstrated. No hidden absolute identifier
or channel callback is used to tell the protocols which sequence cycle a frame
belongs to. Timeout 10 exceeds the maximum 8-unit round trip, so the assignment
experiments do not retransmit solely because of bounded channel delay.

## Experiments and results

Experiment mode always runs both protocols with 200 packets, `N=4`, `k=3`, and
timeout 10 under these conditions:

| Scenario | Loss | Corruption | Additional delay |
| --- | ---: | ---: | ---: |
| A: Reliable | 0 | 0 | 0 |
| B: Packet loss | 0.10 | 0 | 0 |
| C: Mixed errors | 0.05 | 0.05 | 0.10 |

Probabilities apply to ACKs as well as DATA. Each of the six runs starts with
the same seed. Since protocols send different frames, their subsequent random
draws do not represent exactly matched fault locations. Single seeded runs are
demonstrations, not statistically averaged performance measurements.

The table distinguishes total DATA transmissions (including retransmissions)
from ACK transmissions. A retransmission is one repeated DATA transmission, not
one timeout or window retry. Completion time ends when the final original
packet has been acknowledged; stale events still in the queue do not count.

Generated files, also included for seed 42:

- [Consolidated results](results/results.csv)
- [Retransmission comparison graph](results/retransmissions.png)
- [Four-sentence explanation](results/explanation.txt)

Use `--output-dir PATH` to select another experiment output directory. Experiment
mode fixes the transfer/channel scenarios above; single-transfer settings such as
`--packets` and `--loss` do not override the required scenarios.

## How to read the code

Start with a small reliable GBN transfer and follow this order:

1. In `main.py`, read `create_simulation()` and `run_simulation()`. The first sets
   up a run; the second handles three event types: DATA arrival, ACK arrival, and
   timeout. The variable `protocol` refers to the imported `gbn` or `sr` module.
2. In `simulation.py`, read `create_state()` to see the shared state dictionary.
   `schedule_event()` pushes a timestamped event onto the heap. `log_event()` and
   `deliver_packet()` handle logging and application delivery.
3. In `channel.py`, read `create_frame()` and `send_frame()`. Both DATA and ACKs
   use this channel, which schedules an arrival event unless the frame is lost.
4. In `gbn.py`, follow `send_available_packets()`, `receive_data()`, `receive_ack()`,
   and `handle_timeout()`. Each uses the sender or receiver dictionary initialized
   at the top of the module. The ACK calculation includes a wrapping example.
5. Read the matching functions in `sr.py` and compare the differences: individual
   ACKs, separate packet timers, and buffering. `deliver_buffered_packets()` stops
   at the first missing packet.
6. Finally, read `results.py` for the experiment loop, counters, CSV, and graph.

The event queue is a min-heap of `(event_time, event_order, event)` tuples. The
earliest time is processed first; the order counter breaks ties without comparing
event dictionaries. An arrival event contains its type and a frame. A timeout
event contains a timer version, plus the local packet index for SR. The main loop
dispatches these records directly to the protocol functions.

For example, a successful DATA/ACK exchange proceeds as follows:

1. The sender calls `send_packet()`, which creates a frame and calls `send_frame()`.
2. The channel schedules a `data_arrival` event.
3. The main loop advances the clock to that event and calls `receive_data()`.
4. The receiver sends an ACK through the channel, creating an `ack_arrival` event.
5. The main loop calls `receive_ack()`, which updates the sender window and sends
   any new packets that now fit. A timeout follows a separate event path if needed.

## State and function interfaces

The `state` dictionary belongs to one run. Its main sections are:

| Key | Purpose |
| --- | --- |
| `settings` | Protocol, packet count, probabilities, window, timing, and seed |
| `sender` | Sender base, next original packet, and protocol-specific ACK/timer state |
| `receiver` | Next expected position, plus SR's buffer |
| `channel` | Last scheduled DATA and ACK arrival times, used for FIFO delivery |
| `events`, `time` | Pending events and the current simulated clock |
| `original_payloads`, `delivered_payloads` | Original messages and ordered application output |
| `statistics` | DATA attempts, retransmissions, delivered count, ACK attempts, and completion time |

`sequence_space`, `next_event_order`, and `random_generator` support sequence
wrapping, event ordering, and reproducible randomness. Sender and receiver fields
are documented next to their initialization inside each protocol module. Frames
are separate dictionaries containing only `frame_type`, `sequence_number`,
`payload`, and `checksum`.

To run from Python, pass a settings dictionary to `simulate()`. Omitted settings
use the command-line defaults. It returns a statistics dictionary:

```python
from main import simulate

statistics = simulate({
    "protocol": "sr",
    "packets": 20,
    "loss": 0.10,
    "seed": 42,
    "quiet": True,
})
print(statistics["retransmissions"])
```

For inspection, `create_simulation(settings)` returns the initialized state and
`run_simulation(state)` runs it once. All mutable state is local to that run;
the caller's settings dictionary is copied.

The application is entirely procedural. No ready-made reliable-transport library
is used.
