# Acquisition and Raw Recording

## Separation of source and recorder

Acquisition is split into two layers:

- an `AcquisitionBackend` adapts one source to a common batch contract;
- `AcquisitionRecorder` owns lifecycle, concurrency, raw persistence, marker
  requests, health logging, and summary metadata.

This prevents BrainFlow or LSL details from leaking into the engine and keeps
file-format behavior consistent across hardware and simulated sources.

## Backend contract

An acquisition backend exposes:

- its validated `DeviceProfile`;
- channel names, marker capability, and source metadata;
- `prepare`, `start`, non-blocking `read_available`, `insert_marker`, `stop`,
  and `close` lifecycle operations.

`read_available` returns an immutable `SampleBatch` with equal-length source
timestamps, corrected timestamps, and fixed-width sample rows. It may return
`None` when no samples are currently available. The contract does not impose a
NumPy dependency on the rest of the application.

The factory selects an adapter solely from `DeviceProfile.backend`:

| Backend setting | Adapter | Clock | Marker behavior |
|---|---|---|---|
| `synthetic` | In-process deterministic generator | real or virtual | marker channel generated locally |
| `brainflow_synthetic` | BrainFlow adapter, synthetic board ID | real | BrainFlow `insert_marker` |
| `replay` | BrainFlow adapter, playback-file board | real | BrainFlow `insert_marker` |
| `cyton` | BrainFlow adapter, Cyton board | real | BrainFlow `insert_marker` |
| `lsl` | LSL `StreamInlet` adapter | real | cannot modify upstream stream; sidecar only |

## Native synthetic backend

The native source is designed for deterministic end-to-end runs. Each channel
is a closed-form combination of alpha/beta sinusoids, 50 Hz line component,
and deterministic pseudo-noise derived from sample/channel indexes. It requires
no external library or process.

Available samples are calculated from elapsed protocol-clock time. The backend
also appends a marker channel and queues marker requests against their
monotonic timestamps. Because it follows `VirtualClock`, a complete long
protocol can produce correctly sized raw data without waiting in real time.

This mode tests the application's own configuration, timing, event, recording,
and validation path. It does not test a network transport or BrainFlow.

## BrainFlow adapter

One adapter serves BrainFlow synthetic, replay, and Cyton profiles. Imports are
lazy so users without the acquisition extra can still validate and preview
protocols.

During preparation it:

1. maps supported connection fields into `BrainFlowInputParams`;
2. resolves replay file paths relative to the device profile;
3. prepares the board session;
4. obtains the master board's row count, timestamp, marker, package-number, and
   sampling-rate metadata;
5. creates a name for every BrainFlow board row, replacing configured EEG rows
   with montage labels and naming timestamp/marker/package rows explicitly.

Reads drain `get_board_data()` and transpose BrainFlow's row-major matrix into
sample rows. All board rows—not only configured EEG channels—are preserved in
the raw CSV. Marker requests call BrainFlow's `insert_marker`; the event's
monotonic timestamp cannot be supplied to BrainFlow, so the sidecar retains the
request/event times.

Package number modulo 256 is used for discontinuity detection because some
BrainFlow board timestamps arrive in bursts or with quantization that would
produce false timestamp-gap warnings.

## LSL adapter and synthetic publisher

The LSL inlet resolves a stream by configured name when available, otherwise
by type, then optionally filters by type. It validates channel count before
opening the inlet. On connection it records stream identity, host, nominal
sample rate, and one measured LSL time correction.

Each read pulls a non-blocking chunk. The batch retains both original LSL
timestamps and `source timestamp + measured correction`. Only the stream's
channel values are persisted because an inlet exposes samples, not BrainFlow
board rows.

An inlet cannot inject protocol markers into the producer's EEG stream, so
`insert_marker` returns false. Every event is still written to
`acquisition-markers.jsonl` with `embedded_by_backend: false`.

`publish-lsl-synthetic` is a separate, real-time source process. It publishes
the same deterministic EEG-like values with LSL timestamps and channel
metadata, allowing the inlet/transport path to be tested without Cyton. It is
not interchangeable with the in-process synthetic backend: it requires two
processes, real time, discovery, transport, and LSL clock handling.

## Recorder lifecycle

`AcquisitionRecorder.start()` opens marker/health logs, prepares the backend,
creates the raw CSV/header, starts the source, and records start timestamps.
For real clocks it launches:

- `eeg-source-reader`, which polls the backend and places batches on a bounded
  queue;
- `eeg-raw-writer`, which serializes queued batches to CSV.

The default queue holds 64 batches. A full queue drops the incoming batch,
increments dropped batch/sample counters, and emits a health error. Blocking
the source indefinitely would risk losing data in a less visible location, so
the loss is made explicit.

During shutdown, the recorder stops/joins the reader, asks the backend to stop,
performs a final read, signals and joins the writer, closes the backend, writes
metadata, and closes all files. Startup and shutdown are idempotence-guarded;
a recorder cannot be restarted after close.

## Raw CSV semantics

The header is:

```text
sample_index,receipt_monotonic_seconds,receipt_time_utc,source_timestamp,corrected_source_timestamp,<source channels...>
```

- `sample_index` is a zero-based contiguous index assigned by the writer.
- Receipt monotonic/UTC values are captured once per read batch, so all rows in
  that batch share them. They describe application receipt, not individual
  sampling instants.
- `source_timestamp` is supplied or derived by the backend.
- `corrected_source_timestamp` currently equals source time for native
  synthetic and BrainFlow, and adds the measured LSL correction for LSL.
- Remaining values are written unchanged from `SampleBatch`, formatted for CSV
  but not filtered, re-referenced, resampled, or QC-cleaned.

The recorder estimates effective rate from the first/last corrected source
timestamps. It detects BrainFlow package-sequence gaps when sequence metadata
exists; otherwise it flags non-increasing timestamps or gaps greater than 2.5
configured sample intervals.

## Marker model

Every protocol event reaches `AcquisitionRecorder.emit` synchronously. The
recorder:

1. requests backend marker insertion;
2. records whether the backend accepted embedding and any error;
3. appends a sidecar record connecting event sequence/type/code and event time
   to marker-request time;
4. in virtual mode, captures samples now available after the clock jump.

The marker sidecar must contain exactly one matching record for every protocol
event in a complete acquired session. This preserves a backend-neutral
synchronization audit even when native embedding is unavailable.

Embedded markers are inherently source-specific and may land at the next
available sample. Analyses should begin with the structured event timeline and
use embedded/sidecar data to measure alignment rather than assume numerical
marker equality alone proves timing accuracy.

## Health and metadata

`acquisition-health.jsonl` records lifecycle changes and problems with both
monotonic and UTC time: startup, overruns, read/write errors, marker failures,
sequence/timestamp discontinuities, stop/close issues, and final sample count.

`acquisition-metadata.json` summarizes backend/profile identity, configured and
effective rates, channel catalog, sample/drop/discontinuity/error counts,
start/stop timestamps, embedded-marker capability, source-specific metadata,
and final acquisition status.

These are observational records. They never trigger EEG rewriting, automatic
trial rejection, or protocol changes.

## Live monitoring snapshot

The recorder retains a thread-safe, bounded five-second ring of recently
written source rows. `snapshot()` copies at most the requested number of rows
plus recording/sample/drop/gap/error counters, channel names, and latest health
state into immutable `AcquisitionSnapshot`. The experimenter UI renders this
copy; it never tails or locks the authoritative CSV and cannot modify backend
buffers. `flush_pending()` is provided for deterministic tests that need to
wait until already queued virtual samples have reached the writer.

## Current limitations

- LSL time correction is sampled during preparation rather than periodically;
  long recordings may need correction-history tracking.
- The current CSV format prioritizes transparency and inspectability over size
  and high-throughput efficiency.
- Queue capacity and polling interval are constructor defaults, not yet
  configuration fields.
- Cyton behavior and marker alignment still require the roadmap's hardware
  acceptance run.
- Device gain, filters, firmware, impedance/contact, and richer LSL channel
  metadata are persisted only when the adapter currently exposes them; several
  fields remain future work.
