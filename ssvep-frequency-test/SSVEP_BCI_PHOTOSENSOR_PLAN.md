# Photoresistor → Screen-Frequency Check for `ssvep-bci`

This document adapts the Oz-Speller photosensor pipeline
(`PHOTOSENSOR_FREQUENCY_CHECK.md`, reproduced in `../ssvep-bci/`) to the
`ssvep-bci` experiment application. The core approach is unchanged — sample the
luminance of the flickering stimulus as fast as the Arduino will go, timestamp
every sample on the PC, inspect the recorded light waveform in the time domain
(frame-skips) and frequency domain (FFT peak = actual frequency) — but the
**clock synchronization** is different, because the two projects use different
stimulus stacks:

| | Oz Speller | `ssvep-bci` |
|---|---|---|
| Stimulus stack | PsychoPy | PySide6 + OpenGL |
| Shared time base | `pylsl.local_clock()` on both sides | `time.perf_counter()` on both sides |
| Trial metadata | hand-written `meta.csv` from the stimulus | `events.jsonl` already written by the app |
| Recorder launch | subprocess spawned by the stimulus | standalone script, run manually |

Pipeline overview:

```
[Flickering stimulus on screen]
        │  light (photoresistor sits on it)
        ▼
[Arduino Uno R3: analogRead → Serial.println (free-running)]
        │  one ASCII sample per line @ 19200 baud
        ▼
[record_photosensor.py: read lines, timestamp with perf_counter → light_amp.csv]
        │
        ▼
[run_check.py / notebook: align to events.jsonl, time-domain + FFT analysis]
```

---

## Step 1 — Hardware

Identical to the Oz plan:

- **Sensor**: TEMT6000 ambient-light sensor (an LDR works too).
- **Wiring** (TEMT6000): Collector → **5V**, Emitter → **A0**, Emitter →
  10 kΩ pull-down to GND. Brighter light → higher `analogRead`.
- For a plain LDR use a voltage divider (inverted response is fine — only the
  timing of transitions matters).
- Place the sensor directly on a stimulus on the screen. For verification use a
  **single-stimulus config** (e.g.
  `four-frequency-single-circle-4min.yaml` or a copied config with one stimulus)
  so the sensor sees exactly one flicker. No code changes are needed to draw a
  "photosensor dot" — the sensor just sits on the stimulus.

## Step 2 — Arduino sketch (free-running sampler)

File: `arduino_light_sensor_sketch/arduino_light_sensor_sketch.ino` — based on
Oz, but sends the **full 10-bit** `analogRead` as an ASCII line via
`Serial.println`, `Serial.flush()` self-throttles to ≈400–500 Hz (4–5 chars per
line @ 19200 baud; still >10× the SSVEP Nyquist). Oz's original `Serial.write`
sent `analogRead % 256`, which wraps readings above 255 (e.g. 256 → 0) and hid
the real light level once the sensor is pressed on a bright stimulus — keep the
10-bit ASCII format. No experiment logic; the PC assigns timestamps.

## Step 3 — PC-side recorder

File: `record_photosensor.py`

```bash
python record_photosensor.py --port /dev/ttyACM0 --out ./measurement_01
```

```python
import serial
t_mono = time.perf_counter()   # same clock the app uses for events.jsonl
t_wall = time.time()           # wall UTC for cross-validation
```

Key points:

- Reads **one newline-terminated ASCII line at a time**, timestamps each with
  `time.perf_counter()` and wall-UTC epoch, writes rows to `light_amp.csv` in
  batches. Partial lines are buffered until the newline arrives, so read stalls
  cannot corrupt a sample.
- **Discards the first 100 samples** (serial warm-up); on sample #100 it writes
  one diagnostic sync line to `photosensor_sync.csv`.
- Stores the raw 10-bit reading (0–1023). The Oz `>100 → 0` garbage hack is
  **not** applied in the recorder — thresholding is done in analysis so real
  amplitude information is preserved.
- `--seconds N` stops automatically; otherwise Ctrl-C.
- **Bursty reads are expected.** The OS serial driver buffers bytes, so the PC
  read loop drains them in bursts and the raw per-byte timestamps are locally
  compressed. The Arduino still samples at a near-constant rate, so the analysis
  re-times the recording onto a uniform grid (`retime_uniform`) using the
  span-based rate — see Step 7.

**Port**: `COM3` on Windows, `/dev/ttyACM0` (Uno) or `/dev/ttyUSB0` (FTDI) on
Linux. The baud rate must match the sketch (19200).

## Step 4 — Clock synchronization (the key difference)

In Oz, both the stimulus and the recorder timestamp with `pylsl.local_clock()`.
`ssvep-bci` timestamps its events with `time.perf_counter()`
(`RealClock.monotonic` in `src/ssvep_bci/runtime/clock.py`). The adaptation:

1. **Same clock function, same machine.** `time.perf_counter()` is backed by a
   system-wide monotonic counter on every OS `ssvep-bci` supports (Linux
   `CLOCK_MONOTONIC`, Windows QPC, macOS `mach_absolute_time`). The Arduino plugs
   into the same PC that runs the app, so the recorder subprocess's
   `perf_counter()` values are directly comparable to the `monotonic_timestamp`
   in `events.jsonl`. Trial alignment is a plain lookup — no offset is needed,
   and this mirrors Oz's single-clock design.
2. **`events.jsonl` replaces `meta.csv`.** The app already logs a
   frame-swap-confirmed `stimulus_onset` per trial (emitted only after the first
   *on* frame is actually displayed, `src/ssvep_bci/runtime/protocol.py`), with
   `stimulus_id`, `trial_number`, `presentation_id`, `monotonic_timestamp`, and
   `wall_clock_timestamp_utc`. Per-trial frequency/phase comes from the session's
   `experiment-config.yaml` snapshot by `stimulus_id`. **No stimulus code changes
   are required.**
3. **Cross-validation.** The recorder also writes wall-UTC per sample, so the
   analysis can map each onset by wall clock and compare with the monotonic
   mapping. The residuals should be consistent (sub-ms spread); a large spread
   indicates the clocks disagree and the recording is not trustworthy.

## Step 5 — The data files

All analysis reads a session folder from `ssvep-bci run` plus the light data.

### `light_amp.csv` (from `record_photosensor.py`)

Header: `time_monotonic, time_wall, light_amp`. One row per received line.

- `time_monotonic`: `time.perf_counter()` at the moment the PC **read** the line
  (not when the Arduino sampled it — a few ms of serial latency exists).
- `time_wall`: Unix epoch seconds, `time.time()` at the same moment.
- `light_amp`: integer 0–1023, the raw 10-bit photoresistor reading.

### `photosensor_sync.csv` (from `record_photosensor.py`)

Diagnostic: `kind, time_monotonic, time_wall` with `start`, `warmup_end`, and
`end` lines. A fixed reference for latency sanity checks; not required for
alignment.

### `events.jsonl` (already written by the app)

- `stimulus_onset` rows carry the authoritative trial-start timestamps in the
  same clock domain as `time_monotonic`.
- `experiment-config.yaml` (session snapshot) maps `stimulus_id` →
  `frequency_hz`, `phase_offset_radians`, `duty_cycle`.

## Step 6 — Analysis

Two entry points, both backed by `photosensor_check/analysis.py`:

- `run_check.py --session <session_dir> --light <light_amp.csv>` — headless
  report: `summary.csv`, `time_domain.png`, `fft_classes.png`.
- `notebooks/photosensor_frequency_check.ipynb` — interactive walk-through.

### 6a. Load and segment trials

```python
trials  = pc.build_trials(events, stimuli)        # onsets from events.jsonl
frame   = pc.load_light_amp(LIGHT_CSV)
sfreq   = pc.calibrate_sfreq(frame)               # 1 / median(dt), measured
segments = pc.segment_trials(frame, trials, latency=0.07)
```

- `latency = 0.07 s` skips serial latency at trial onset (the same fudge Oz
  used; the alignment table reports the actual residual so it can be tuned).
- The window is `[onset, offset] + latency` using the frame-swap-confirmed
  onset/offset events, so it matches what the screen actually displayed.

### 6b. Time-domain check (frame-skips)

Plot `light_amp` against time for each trial. Uniform on/off pulse widths = good
timing; one ~2× wide pulse = a skipped frame. `pc.detect_frame_skips(...)` counts
these programmatically. This is the most reliable frame-skip detector because a
skip is a timing anomaly a frequency peak alone would miss.

### 6c. Frequency-domain check (FFT)

The PSD peak must land on the target frequency, and a square wave shows odd
harmonics (3f ≈ 1/3, 5f ≈ 1/5 of the fundamental amplitude):

```python
freqs, psd = pc.power_spectrum(seg.x, sfreq)
peak = pc.detect_peak(freqs, psd)
pc.check_harmonics(freqs, psd, trial.frequency_hz)   # -> {3: True/False, ...}
```

Long ssvep-bci trials (4–5 s) give ~0.2 Hz bins — comfortably separating the
closely spaced 8.25/9.75/12.75/14.25 Hz set.

### 6d. Summary

`pc.summarize(segments, sfreq, tolerance_hz=0.25)` produces a per-trial table:
target vs detected frequency, Δ Hz, PASS/FAIL, frame-skip count, harmonic
presence.

## Step 7 — Calibrating the actual sample rate

`sfreq` is never configured — it is measured per recording, and because the
Arduino is a free-running sampler its rate is constant regardless of how the PC
delivers the bytes. The recorder reads in bursts (OS buffering), so the median
inter-sample time is **not** a reliable estimate; the span-based rate is:

```python
sfreq, frame = pc.retime_uniform(frame)      # re-times onto a uniform grid
```

`retime_uniform` computes `sfreq = (n - 1) / (t_last - t_first)` and restores
per-sample times as `t_k = t_0 + k / sfreq`. This keeps FFTs and trial
segmentation valid even when the raw timestamps are bursty. `sampling_diagnostics`
reports both rate estimates and flags burstiness. ≈400–500 Hz at 19200 baud with
the ASCII sketch (line length 4–5 chars); it varies by run and hardware.

PC serial stalls matter too. The Arduino free-runs through a PC stall (its
`Serial.flush()` only waits for the shift register; the OS/USB buffer holds the
bytes), so the stall adds wall time to the span without adding samples. Using
`t_last - t_first` unchanged then under-estimates `sfreq` and every measured
frequency scales down with it — a 1.5 s stall on a 30 s recording biased the
rate by ~5 % and made an 8.25 Hz stimulus read 7.84 Hz. `calibrate_sfreq`
therefore excludes inter-sample gaps longer than 50 ms (`_serial_gaps`) and
divides by active time only; `sampling_diagnostics["gap_count"]` /
`["gap_seconds"]` report the stalls, and `run_check.py` prints a note when it
corrects for them.

## Step 8 — Workflow

1. Plug in the Arduino, place the sensor on the stimulus, upload the sketch.
2. Start the recorder: `python record_photosensor.py --port /dev/ttyACM0 --out ...`
3. Run the experiment normally: `ssvep-bci run --config <cfg> --participant ...`
4. Stop the recorder after the session finishes.
5. Run `run_check.py --session <session_folder> --light <light_amp.csv>` (or the
   notebook) and read the summary.

## Dependencies

```bash
python -m pip install pyserial pandas numpy matplotlib pyyaml jupyter
```

(`scipy` is not required.)

## Caveats

- **FFT resolution = 1 / window seconds.** The window is the full trial as
  recorded in `events.jsonl` (`onset`→`offset`), which tracks
  `protocol.stimulation_seconds` in the experiment config — set it to 10 s for
  0.1 Hz resolution. Aborted trials (missing offset) fall back to the same
  configured duration.
- **Sample rate is constant on the Arduino but the reads are bursty.** Measure it
  per recording from the total span (`retime_uniform`), never from the median
  inter-sample time. Long PC serial stalls inflate the span — the rate is computed
  over active time only, and a stall note is printed when one is found.
- **A saturated sensor still passes the FFT.** The frequency check works even on
  a near-saturated trace (tiny on/off contrast), but the time-domain frame-skip
  check does not. `run_check.py` and the notebook warn when `light_amp` has almost
  no contrast — that means the sensor is saturated or not on the stimulus, and the
  frame-skip numbers are meaningless.
- **Serial latency** (~tens of ms) exists between the screen showing a frame and
  the PC timestamping its light sample — that is what `latency` skips, and the
  alignment residuals make it measurable.
- **Clock comparability assumes the recorder and the app run on the same PC.**
  If they ever run on different machines, the wall-clock column is the shared
  reference but becomes vulnerable to clock jumps — keep them on one machine.
- **Frequency vs. timing are two different checks.** FFT peak = frequency
  identity; time-domain pulse widths = frame-skips. Do both.
- The recorder reads the raw 8-bit value; amplitude thresholds are applied in
  analysis, so genuine luminance structure is not destroyed up front.

## Reference files

| File | Role |
|------|------|
| `arduino_light_sensor_sketch/arduino_light_sensor_sketch.ino` | Free-running Arduino sampler (unchanged from Oz) |
| `record_photosensor.py` | PC recorder → `light_amp.csv`, `photosensor_sync.csv` |
| `photosensor_check/analysis.py` | Segmentation, alignment, FFT, frame-skip helpers |
| `run_check.py` | Headless report (`summary.csv`, `time_domain.png`, `fft_classes.png`) |
| `notebooks/photosensor_frequency_check.ipynb` | Interactive walk-through |
| `../ssvep-bci/.../events.jsonl`, `experiment-config.yaml` | Trial metadata from the session |
