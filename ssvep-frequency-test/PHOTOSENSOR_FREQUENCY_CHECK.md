# Photoresistor → Screen-Frequency Check: Full Pipeline

This document describes, end to end, how the Oz-Speller project checks that the
frequency actually displayed on the screen matches the target SSVEP flicker
frequency, using an Arduino + photoresistor (TEMT6000). It is written so you can
reproduce the same setup for your own custom experiment, with the parts that are
experiment-specific clearly flagged so you can strip them out.

The entire approach is: **sample the luminance of the flickering stimulus as
fast as the Arduino will go, timestamp every sample on the PC, then inspect the
recorded light waveform in the time domain (frame-skips) and frequency domain
(FFT peak = actual frequency).**

Pipeline overview:

```
[Flickering stimulus on screen]
        │  light (photoresistor sits on it)
        ▼
[Arduino Uno R3: analogRead → Serial.write (free-running)]
        │  one raw byte per sample @ 19200 baud
        ▼
[PC: read bytes, timestamp with local_clock → light_amp.csv]
        │
        ▼
[Analysis: time-domain plot (frame-skips) + FFT (frequency identity)]
```

---

## Step 1 — Hardware

- **Sensor**: TEMT6000 ambient-light sensor (the README names it explicitly).
  An LDR works too — anything whose output tracks luminance.
- **Wiring** (TEMT6000):
  - Collector → **5V**
  - Emitter → **A0** on the Arduino
  - Emitter → **10 kΩ pull-down resistor to GND**
  - Brighter light → higher voltage on A0 → higher `analogRead`.
- For a plain LDR, use a voltage divider: LDR from **5V** to A0, ~10 kΩ from A0
  to GND (inverted response is fine — only the timing of transitions matters).
- Place the sensor directly on top of the flickering stimulus on the screen.

---

## Step 2 — Arduino sketch (free-running sampler)

File: `src/arduino_light_sensor_sketch/arduino_light_sensor_sketch.ino`

```cpp
#define LIGHTSENSORPIN A0 //Ambient light sensor reading

void setup() {
  pinMode(LIGHTSENSORPIN, INPUT);
  Serial.begin(19200);
}

void loop() {
  unsigned char reading = analogRead(LIGHTSENSORPIN); //Read light level
  Serial.write(reading);
  Serial.flush();
}
```

What it does, per loop iteration:

1. `analogRead(A0)` returns 0–1023 (10-bit).
2. `(unsigned char)` truncates to 0–255 (8-bit, wraps for values >255).
3. `Serial.write(reading)` sends the value as a **raw binary byte** (not ASCII).
4. `Serial.flush()` blocks until the byte has left the TX pin.

Because of step 4, the loop self-throttles to roughly the serial throughput:
19200 baud / 8N1 ≈ **1920 bytes/s ≈ ~1.9 kHz**. The loop is **free-running** —
there is **no fixed sample clock**. The PC assigns timestamps, not the Arduino.

This file contains no experiment logic and can be copied into any project
verbatim. The only constraint is that the PC reader must use the same baud rate.

---

## Step 3 — PC-side recorder

File: `scripts/run_arduino_photosensor.py`

```python
import serial, threading, multiprocessing, time
from pylsl import local_clock
import numpy as np

arduino = serial.Serial(port='COM3', baudrate=19200, timeout=.1)
arduino_call_num = 0
datalist = []
data_save_num = 0

with open("light_amp.csv", 'w') as csv_file:
    csv_file.write('time, light_amp\n')

def record_light_amp():
    global arduino_call_num, datalist, data_save_num
    while True:
        try:
            data = int.from_bytes(arduino.read(), "big")
            if data > 100:
                data = 0
            if arduino_call_num < 100:          # discard 100 warm-up samples
                arduino_call_num += 1
                continue
            elif arduino_call_num == 100:       # one sync marker in meta.csv
                arduino_call_num += 1
                with open("meta.csv", 'w') as csv_file:
                    csv_file.write('1,1,' + str(local_clock()) + '\n')
            datalist.append([local_clock(), data])
            data_save_num += 1
            if data_save_num > 7000:            # flush in batches of 7000
                datalist_np = np.array(datalist)
                with open("light_amp.csv", 'a') as csv_file:
                    np.savetxt(csv_file, datalist_np, delimiter=', ',
                               fmt=['%.14e', '%d'])
                data_save_num = 0
                datalist = []
        except UnicodeDecodeError and ValueError:
            pass

record_light_amp()
```

Key points:

- Reads **one byte at a time** from the serial port.
- Timestamps each byte with `pylsl.local_clock()` — a monotonic clock, the same
  clock the stimulus uses, so light data can be aligned to trial starts.
- **Discards the first 100 samples** (serial warm-up).
- On sample #100 it writes a one-time sync line to `meta.csv`.
- `if data > 100: data = 0` is a garbage-rejection hack: anything above 100 is
  zeroed. The recorded signal therefore only preserves "dark vs. light"
  transitions, not true amplitude.
- Appends to `light_amp.csv` in batches of 7000 rows.

**Port**: `COM3` is hardcoded (Windows). On Linux use `/dev/ttyACM0` (Uno) or
`/dev/ttyUSB0` (FTDI). In `oz-speller.py` this recorder is launched as a
subprocess right before the stimulus starts:

```python
if use_arduino:
    Popen([executable, os.path.join(os.getcwd(), 'scripts', 'run_arduino_photosensor.py')])
    time.sleep(2)
```

---

## Step 4 — The stimulus (PsychoPy)

File: `scripts/oz-speller.py` (the relevant knobs):

```python
use_arduino = False        # set True to launch the recorder
use_photosensor = False    # set True to draw the photosensor dot
flash_mode = 'square'      # 'square', 'sine', 'chirp', 'dual band'
refresh_rate = 60.02       # monitor refresh, Hz
stim_duration = 1.2        # seconds of stimulation per trial
isi_duration = 1           # seconds of fixation before/after each trial
```

The flicker is **precomputed per frame**, not driven by a timer:

```python
trial = signal.square(2 * np.pi * flickering_freq *
                      (frame_indices / refresh_rate) + phase_offset * np.pi)
```

Each frame it draws the flickering square and — when `use_photosensor=True` —
a small white **photosensor dot** (`create_photosensor_dot`, a white circle in
the corner) that is white when the square is white and black when it is off.
The photoresistor is placed on this dot so it sees the exact on-screen
luminance.

At frame 0 of every trial, one line is appended to `meta.csv`:

```python
csv_file.write(str(flickering_freq) + ', ' + phase_offset_str + ', ' +
               str(local_clock()) + '\n')
```

The square wave is quantized to whole frames (`frame_indices / refresh_rate`),
so the *actual* on-screen frequency is the frame-quantized version of the
target — e.g. at 60 Hz, an 8 Hz target is exactly 7.5 frames/cycle, etc. The
photoresistor is what measures the realized frequency.

---

## Step 5 — The two data files

Both are written to the current working directory (the repo root when run
normally), then researchers manually copy them into a per-run folder under
`data/photosensor_recordings/pilot_data/<run>/` for analysis.

### `light_amp.csv`

Header: `time, light_amp`. One row per received byte.

- `time`: seconds, `local_clock()` value at the moment the PC **read** the byte
  (not when the Arduino sampled it — a few ms of serial latency exists).
- `light_amp`: integer 0–255, the 8-bit truncated photoresistor reading (with
  the `>100 → 0` filter applied).

### `meta.csv`

Format: `freq, phase, time`.

- One sync line `1,1,<local_clock>` written by the recorder at sample #100.
- One line per trial `<freq>, <phase>, <local_clock>` written by the stimulus at
  frame 0 of the stimulation.

Because both files use `local_clock()`, trial windows can be cut from the light
data by matching the trial start timestamps.

---

## Step 6 — Analysis

Analysis notebook: `notebooks/Pilot_Photosensor_32-class_Vis[Simon].ipynb`.

### 6a. Load and segment trials

```python
sfreq = 1568          # nominal sample rate, Hz — see Step 7
duration = 1.0        # seconds of stimulation window per trial
light_amp = pd.read_csv(data_path + 'light_amp.csv').astype(float)
meta = np.loadtxt(data_path + 'meta.csv', delimiter=',', dtype=float)
trials = meta[1:, :2]
times = meta[1:, 2]

def load_light_amp_temp_function(light_amp, meta, classes,
                                 stim_duration=4, filter=False, sfreq=1568):
    times = meta[1:, 2]
    stim_end = int(stim_duration * sfreq)
    light_amp = np.array([
        light_amp.loc[light_amp['time'] >= t + 0.07]
                 .drop(columns=['time']).to_numpy()[:stim_end].T
        for t in times])  # 0.07 s offset: serial-port latency
    if filter:
        light_amp = mne.filter.filter_data(light_amp, sfreq=sfreq,
                                           l_freq=5, h_freq=49,
                                           verbose=0, method='fir')
    # ... regroup trials by (freq, phase) class ...
    return light_amp
```

Notes:

- **`+ 0.07`** skips ~70 ms at the start of each trial — a fudge for serial
  latency between the stimulus onset timestamp and the light data actually
  reflecting it.
- **`stim_end = int(stim_duration * sfreq)`** — the per-trial window is exactly
  `stim_duration` × `sfreq` samples.
- Optional 5–49 Hz band-pass filter when `filter=True`.

### 6b. Time-domain check (the README figures)

The README (`README.md:154-172`) shows what the recorded waveform should look
like when there are no frame-skips, versus when frames are skipped:

- `reports/figures/good_timing.png` — a **clean, regular square wave**. Every
  "on" and "off" half-cycle has the same width (same number of frames/samples).
- `reports/figures/skip1.png`, `skip2.png`, `skip3.png` — frame-skips: one
  half-cycle is ~2× longer because a frame was presented for two frame
  durations instead of one. Uneven pulse widths are the signature of a skipped
  frame.

To produce these yourself, simply plot `light_amp` against `time` over a
stimulation window:

```python
import matplotlib.pyplot as plt
import pandas as pd

df = pd.read_csv('light_amp.csv')
t = df['time'] - df['time'].iloc[0]
x = df['light_amp']
plt.figure(figsize=(24, 1))
plt.plot(t, x)
plt.xlabel('time (s)'); plt.ylabel('light_amp')
plt.xlim(0, 1.0)
plt.show()
```

Uniform pulse widths → good timing. One long pulse → a frame-skip. This is the
most reliable frame-skip detector because a skip shows up as a timing anomaly
that a frequency-domain peak alone would miss.

### 6c. Frequency-domain check (FFT)

This is how they "ensure the frequency is right": the FFT peak of the light
waveform must land on the target frequency.

```python
from numpy.fft import rfft, rfftfreq

psd = 1/750 * np.abs(fft(light_amp))[:, :, :, :int(sfreq*duration)//2]
freqs = np.linspace(0.0, sfreq / 2, int(sfreq * duration // 2))

# plot PSD per class; the peak should sit at the target frequency
```

```python
# pick the exact FFT bin nearest a target frequency
target_freq = 8.0
bin_idx = np.abs(freqs - target_freq).argmin()
```

Expected signature of a correct square-wave render:

- A **fundamental peak at the target frequency f**.
- **Odd harmonics at 3f, 5f, ...** (a square wave has no even harmonics).

```python
# verify harmonics (square wave signature)
fund = np.abs(freqs - target_freq).argmin()
for h in (3, 5):
    near = np.abs(freqs - target_freq * h).argmin()
    if psd[near] > 0.3 * psd[fund]:
        print(f"harmonic {h}f present (good square wave)")
```

### 6d. Complex spectrum (optional)

They also plot the complex FFT value at each class's target frequency bin per
trial. Each trial's phase angle should cluster per class:

```python
complex_spectrum = 1/750 * fft(light_amp)[:, :, :, :int(sfreq*duration)//2]
for i in range(n_classes):
    bin_idx = np.abs(freqs - classes[i][0]).argmin()
    plt.scatter(np.real(complex_spectrum[:, i, 0, bin_idx]),
                np.imag(complex_spectrum[:, i, 0, bin_idx]),
                label=str(classes[i]))
```

Tight phase clusters per class confirm both frequency **and** phase are
delivered as intended.

---

## Step 7 — Calibrating the actual sample rate

The Arduino is free-running, so `sfreq` is **not** configured anywhere — it is
measured empirically from the recorded timestamps. They check it with cells
like:

```python
num = 50000
light_amp['time'][num+296] - light_amp['time'][num]   # ≈ 0.99 s → ≈ 296 Hz
```

i.e. 296 consecutive samples spanned ≈ 1 second, so that recording ran at
~296 Hz. (The notebook hardcodes `sfreq = 1568` for a different recording;
the number varies by run and hardware. **Always measure it for your own
recording.**)

Robust way to compute it for your data:

```python
import numpy as np, pandas as pd
df = pd.read_csv('light_amp.csv')
t = df['time'].to_numpy()
dt = np.median(np.diff(t))          # robust to outliers
sfreq = 1.0 / dt
print(f"sample rate ≈ {sfreq:.0f} Hz")
```

---

## Step 8 — Standalone minimal recipe (your own experiment)

If you only want to verify the frequency of a steady flicker (no trials, no
classes), strip everything down to:

**Arduino** — copy the sketch from Step 2 unchanged.

**Recorder** (no pylsl dependency):

```python
import serial, time

s = serial.Serial('/dev/ttyACM0', 19200, timeout=1)   # your port
with open('light_amp.csv', 'w') as f:
    f.write('time, light_amp\n')
start = time.time()
i = 0
while i < 5000:                 # ~5 s; Ctrl-C to stop early
    b = s.read(1)
    if not b:
        continue
    v = b[0]
    if v > 100:
        v = 0
    with open('light_amp.csv', 'a') as f:
        f.write(f"{time.time() - start},{v}\n")
    i += 1
```

**Frequency check** (run while the screen shows a steady flicker at `target_hz`):

```python
import numpy as np, pandas as pd

target_hz = 12.0
df = pd.read_csv('light_amp.csv')
t, x = df['time'].to_numpy(), df['light_amp'].to_numpy()
dt = np.median(np.diff(t))
sfreq = 1.0 / dt
x = x - x.mean()
X = np.abs(np.fft.rfft(x * np.hanning(len(x))))       # window to reduce leakage
freqs = np.fft.rfftfreq(len(x), dt)

band = (freqs >= 1) & (freqs <= 40)
peak = freqs[band][np.argmax(X[band])]
print(f"sample rate ~ {sfreq:.0f} Hz")
print(f"target {target_hz:.1f} Hz -> detected peak {peak:.2f} Hz")

fund = np.abs(freqs - target_hz).argmin()
for h in (3, 5):
    near = np.abs(freqs - target_hz * h).argmin()
    if X[near] > 0.3 * X[fund]:
        print(f"  harmonic {h}f present (good square wave)")
```

Detected peak ≈ target within one FFT bin ⇒ the screen renders the right
frequency.

---

## Caveats and practical notes

- **FFT resolution = 1 / window_seconds.** 1 s windows give 1 Hz bins — coarse
  for closely-spaced SSVEP frequencies. Use longer windows (e.g. 4 s → 0.25 Hz)
  or zero-padding.
- **Sample rate is irregular** (PC-side timestamps). Compute it per-recording
  with the median inter-sample time, never assume a fixed value.
- **The `>100 → 0` hack destroys amplitude**, so keep it only if you care about
  transitions, not true luminance levels.
- **Baud rate must match** between the sketch and the PC reader (19200 in their
  setup).
- **Serial latency** (~tens of ms) exists between the screen showing a frame and
  the PC timestamping its light sample — that's why the notebook skips 0.07 s
  at trial onset.
- **`Serial.flush()` on the Arduino** throttles the loop to the baud rate; this
  is the mechanism behind the "free-running" sample rate. To get a *fixed*
  rate, replace the loop body with a `micros()`-based interval (commented-out
  examples exist in the sketch).
- **Frequency vs. timing are two different checks.** FFT peak = frequency
  identity; time-domain pulse widths = frame-skips. Do both.

---

## Reference files

| File | Role |
|------|------|
| `src/arduino_light_sensor_sketch/arduino_light_sensor_sketch.ino` | Free-running Arduino sampler |
| `scripts/run_arduino_photosensor.py` | PC recorder → `light_amp.csv`, `meta.csv` |
| `scripts/oz-speller.py` | PsychoPy stimulus + per-trial `meta.csv` marks |
| `notebooks/Pilot_Photosensor_32-class_Vis[Simon].ipynb` | Segmentation, FFT, complex-spectrum analysis |
| `reports/figures/good_timing.png`, `skip1.png`, `skip2.png`, `skip3.png` | Time-domain frame-skip examples (README) |
