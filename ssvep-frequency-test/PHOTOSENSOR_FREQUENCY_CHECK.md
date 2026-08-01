# SSVEP monitor frequency validation

`ssvep-frequency-test` is an independent Arduino photosensor recorder and
analysis utility for sessions produced by either `ssvep-bci` or
`psychopy-ssvep`. Neither presentation application starts or controls the
recorder.

The recorder and presentation application must run on the same computer. Host
monotonic time aligns the two processes, while the Arduino's own microsecond
clock determines sample spacing and measured frequency. USB buffering or host
CPU load therefore cannot rescale the FFT frequency axis.

All examples below start in the repository's `BCI` directory. Windows examples
use PowerShell paths; Linux examples use Bash paths. Activate the environment
you want to use before running an installed console command.

## Install

The photosensor tool requires Python 3.11 or newer. It can use a different
environment from either presentation application.

### Windows

```powershell
python -m pip install -e ".\ssvep-frequency-test"
python -m pip install -e ".\ssvep-bci[all]"
python -m pip install -e ".\psychopy-ssvep[all]"
```

### Linux

```bash
python3 -m pip install -e './ssvep-frequency-test'
python3 -m pip install -e './ssvep-bci[all]'
python3 -m pip install -e './psychopy-ssvep[all]'
```

Only install the presentation application needed in a given environment.
Conda, Poetry, uv, pipx, a standard virtual environment, or a system Python can
be used; no environment directory name is assumed.

Upload
`ssvep-frequency-test/arduino_light_sensor_sketch/arduino_light_sensor_sketch.ino`
to the Arduino. It samples A0 at a fixed 400 Hz cadence and emits
`sample_index,device_time_us,light_amp` records at 115200 baud. The sample index
detects lost records; the device timestamp remains authoritative even when the
host receives serial data in bursts.

## Validation profiles

Monitor-test profiles are grouped under each application's
`resources/configs/frequency-validation/` folder. Every profile uses one
400 x 400 px square flush with the bottom-right corner unless its name says it
adds distractors. Keep the photosensor fixed on that square.

### `frequency-validation`

This alias selects `frequency-validation/12-75`. It presents 12.75 Hz twice,
for ten seconds per trial, to check that the measurement remains consistent.

Run the baseline profile directly with `ssvep-bci`:

#### Windows

```powershell
python -m ssvep_bci run --config frequency-validation --participant MONITOR_TEST
```

#### Linux

```bash
python3 -m ssvep_bci run --config frequency-validation --participant MONITOR_TEST
```

Run the baseline profile directly with PsychoPy:

#### Windows

```powershell
python -m psychopy_ssvep run --config frequency-validation --participant MONITOR_TEST
```

#### Linux

```bash
python3 -m psychopy_ssvep run --config frequency-validation --participant MONITOR_TEST
```

### `frequency-validation/8-15-square`

This profile presents 8, 9, 10, 11, 12, 13, 14, and 15 Hz in order, then
repeats the complete sequence. Each frequency therefore has two independent
ten-second measurements in one session. During every trial, the measured
bottom-right square is rendered alongside three upper-screen distractors at
8.25, 11.25, and 14.25 Hz, so every target is checked under rendering load.

Run the square-wave sweep with `ssvep-bci`:

#### Windows

```powershell
python -m ssvep_bci run --config frequency-validation/8-15-square --participant MONITOR_TEST
```

#### Linux

```bash
python3 -m ssvep_bci run --config frequency-validation/8-15-square --participant MONITOR_TEST
```

Run the square-wave sweep with PsychoPy:

#### Windows

```powershell
python -m psychopy_ssvep run --config frequency-validation/8-15-square --participant MONITOR_TEST
```

#### Linux

```bash
python3 -m psychopy_ssvep run --config frequency-validation/8-15-square --participant MONITOR_TEST
```

### `frequency-validation/8-15-sinusoidal` (PsychoPy only)

This uses the same target and distractor frequencies, order, duration, and two
repetitions as the square-wave sweep, but modulates every rectangle's
brightness sinusoidally.

#### Windows

```powershell
python -m psychopy_ssvep run --config frequency-validation/8-15-sinusoidal --participant MONITOR_TEST
```

#### Linux

```bash
python3 -m psychopy_ssvep run --config frequency-validation/8-15-sinusoidal --participant MONITOR_TEST
```

### `frequency-validation-with-distractors`

This rendering-load profile keeps the measured 400×400 px square in the same
bottom-right location at 12.75 Hz. Three other 400×400 px rectangles flicker
simultaneously across the upper part of the display at 8.25, 9.75, and
14.25 Hz. The profile repeats two ten-second trials. The legacy bundled name
remains an alias for `frequency-validation/with-distractors`.

The photosensor must remain on the bottom-right square. Comparing its detected
frequency and timing with the baseline profile's 12.75 Hz trial shows whether
rendering the additional rectangles affects the measured rectangle.

Run it directly with `ssvep-bci`:

#### Windows

```powershell
python -m ssvep_bci run --config frequency-validation-with-distractors --participant MONITOR_TEST
```

#### Linux

```bash
python3 -m ssvep_bci run --config frequency-validation-with-distractors --participant MONITOR_TEST
```

Run it directly with PsychoPy:

#### Windows

```powershell
python -m psychopy_ssvep run --config frequency-validation-with-distractors --participant MONITOR_TEST
```

#### Linux

```bash
python3 -m psychopy_ssvep run --config frequency-validation-with-distractors --participant MONITOR_TEST
```

Before using a PsychoPy profile, set `refresh_rate_hz`, `monitor_name`,
and gamma for the display being tested. The bundled defaults target a 60 Hz
monitor with gamma 1.0.

## Record independently

Start the photosensor recorder before starting a presentation. Stop it with
Ctrl+C only after the presentation has finished.

### Windows

```powershell
record-photosensor --port COM3 --out ".\measurements\monitor-test"
```

### Linux

```bash
record-photosensor --port /dev/ttyACM0 --out './measurements/monitor-test'
```

Linux Arduino ports are commonly `/dev/ttyACM0` or `/dev/ttyUSB0`. The recorder
writes `light_amp.csv` and `photosensor_sync.csv` into the selected measurement
directory. `light_amp.csv` contains host receipt times, light amplitude, device
sample index, and device microsecond time. The default baud is 115200; only use
`--baud` when the sketch has been changed to match.

## Portable two-terminal workflow

The `ssvep-frequency-validation` coordinator works in PowerShell, Command
Prompt, Bash, and zsh. It creates timestamped measurement folders, launches a
selected profile, selects the latest session, and writes analysis into that
session.

### Windows

In terminal 1, start recording:

```powershell
ssvep-frequency-validation record --port COM3
```

In terminal 2, run one presentation application:

```powershell
ssvep-frequency-validation run ssvep-bci --participant MONITOR_TEST
ssvep-frequency-validation run psychopy-ssvep --participant MONITOR_TEST
```

After the presentation closes, stop terminal 1 with Ctrl+C. Analyze the
matching application:

```powershell
ssvep-frequency-validation analyze ssvep-bci
ssvep-frequency-validation analyze psychopy-ssvep
```

### Linux

In terminal 1, start recording:

```bash
ssvep-frequency-validation record --port /dev/ttyACM0
```

In terminal 2, run one presentation application:

```bash
ssvep-frequency-validation run ssvep-bci --participant MONITOR_TEST
ssvep-frequency-validation run psychopy-ssvep --participant MONITOR_TEST
```

After the presentation closes, stop terminal 1 with Ctrl+C. Analyze the
matching application:

```bash
ssvep-frequency-validation analyze ssvep-bci
ssvep-frequency-validation analyze psychopy-ssvep
```

`record` creates a timestamped folder under `./measurements`. `run` writes to
`./ssvep-bci/sessions` or `./psychopy-ssvep/sessions`. `analyze` selects the
newest measurement and newest session unless explicit paths are provided.

If the photosensor package is not installed, invoke the coordinator source
file instead.

### Windows

```powershell
python ".\ssvep-frequency-test\frequency_validation.py" --help
```

### Linux

```bash
python3 './ssvep-frequency-test/frequency_validation.py' --help
```

## Presentation application in another environment

The coordinator defaults to the Python interpreter running it. Use `--python`
when the selected application is installed in another environment.

### Windows

```powershell
ssvep-frequency-validation run psychopy-ssvep --python "C:\tools\psychopy-env\Scripts\python.exe"
ssvep-frequency-validation run ssvep-bci --python "C:\tools\ssvep-env\Scripts\python.exe"
```

### Linux

```bash
ssvep-frequency-validation run psychopy-ssvep --python '/opt/psychopy-env/bin/python'
ssvep-frequency-validation run ssvep-bci --python '/opt/ssvep-env/bin/python'
```

## Explicit measurement and session paths

Use an explicit measurement folder when several recordings exist or when
comparing baseline and distractor runs. `--measurement` accepts either the
measurement directory or its `light_amp.csv` file.

### Windows

```powershell
ssvep-frequency-validation analyze ssvep-bci --measurement ".\measurements\monitor-test"
ssvep-frequency-validation analyze psychopy-ssvep --measurement ".\measurements\monitor-test\light_amp.csv"
```

### Linux

```bash
ssvep-frequency-validation analyze ssvep-bci --measurement './measurements/monitor-test'
ssvep-frequency-validation analyze psychopy-ssvep --measurement './measurements/monitor-test/light_amp.csv'
```

Custom roots are supported when the data is outside the repository.

### Windows

```powershell
ssvep-frequency-validation analyze ssvep-bci --measurement-root "D:\BCI data\measurements" --session-root "D:\BCI data\sessions"
```

### Linux

```bash
ssvep-frequency-validation analyze ssvep-bci --measurement-root '/data/bci/measurements' --session-root '/data/bci/sessions'
```

## Baseline-versus-distractor comparison

Give the two recordings explicit names to prevent the wrong latest measurement
from being selected.

### Windows

Baseline run:

```powershell
ssvep-frequency-validation record --port COM3 --name baseline
ssvep-frequency-validation run ssvep-bci --config frequency-validation --participant MONITOR_TEST
ssvep-frequency-validation analyze ssvep-bci --measurement ".\measurements\baseline"
```

Distractor run:

```powershell
ssvep-frequency-validation record --port COM3 --name with-distractors
ssvep-frequency-validation run ssvep-bci --config frequency-validation-with-distractors --participant MONITOR_TEST
ssvep-frequency-validation analyze ssvep-bci --measurement ".\measurements\with-distractors"
```

### Linux

Baseline run:

```bash
ssvep-frequency-validation record --port /dev/ttyACM0 --name baseline
ssvep-frequency-validation run ssvep-bci --config frequency-validation --participant MONITOR_TEST
ssvep-frequency-validation analyze ssvep-bci --measurement './measurements/baseline'
```

Distractor run:

```bash
ssvep-frequency-validation record --port /dev/ttyACM0 --name with-distractors
ssvep-frequency-validation run ssvep-bci --config frequency-validation-with-distractors --participant MONITOR_TEST
ssvep-frequency-validation analyze ssvep-bci --measurement './measurements/with-distractors'
```

Run only one command at a time in each three-command block: keep the recorder
running in terminal 1 while running the presentation command in terminal 2,
then stop the recorder before running the analyzer. Replace `ssvep-bci` with
`psychopy-ssvep` consistently to perform the same comparison with PsychoPy.

## Analyze an explicit session

### Windows

```powershell
run-photosensor-check --session ".\psychopy-ssvep\sessions\SESSION_FOLDER" --light ".\measurements\monitor-test\light_amp.csv"
```

### Linux

```bash
run-photosensor-check --session './psychopy-ssvep/sessions/SESSION_FOLDER' --light './measurements/monitor-test/light_amp.csv'
```

The same command accepts an `ssvep-bci` session folder.

## Analyze the latest session

Point the tool at exactly one application's session root.

### Windows

```powershell
run-photosensor-check --latest --session-root ".\ssvep-bci\sessions" --light ".\measurements\monitor-test\light_amp.csv"
```

### Linux

```bash
run-photosensor-check --latest --session-root './ssvep-bci/sessions' --light './measurements/monitor-test/light_amp.csv'
```

The newest direct child is selected by `manifest.json.created_at_utc`. Session
status is not filtered, so an active, completed, aborted, or failed session can
be selected. Analysis still requires at least one frame-confirmed onset event.

## Session-local results

The analyzer creates `<session>/photosensor-check/` containing:

```text
summary.csv
time_domain.png
fft_classes.png
light_amp.csv
photosensor_sync.csv   # copied when present beside the source light file
```

These post-session analysis files are not inserted into the recording manifest
or checksum list. Re-running the check replaces these known result files and
leaves all other session files untouched.

`summary.csv` reports target and detected frequency, frequency error, PASS/FAIL,
signal range, sample count, duration, frame-skip estimate, and harmonic checks
for each presentation. The default pass tolerance is 0.25 Hz and can be changed
with `--tolerance-hz`. The default 70 ms serial-latency correction can be
changed with `--latency`.

## Practical notes

- Run the recorder and experiment on the same computer so their monotonic
  timestamps are directly comparable.
- Keep the sensor over only the bottom-right measured square.
- Ten-second validation windows provide 0.1 Hz FFT resolution.
- Low contrast means the sensor is saturated, poorly positioned, or seeing too
  much ambient light.
- Frequency identity and frame timing are separate checks: inspect both the FFT
  and time-domain reports.
