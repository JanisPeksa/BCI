# SSVEP monitor frequency validation

`ssvep-frequency-test` is an independent Arduino photosensor recorder and
analysis utility for sessions produced by either `ssvep-bci` or
`psychopy-ssvep`. Neither experiment application starts or controls it.

The recorder timestamps light samples with `time.perf_counter()`. Both SSVEP
applications use that same machine-wide monotonic clock for frame-confirmed
stimulus onset and offset events, allowing the analyzer to align the measured
screen luminance with each trial afterward.

## Install

Install the photosensor tool into any Python 3.11+ environment. It does not
need to be the environment used by either presentation application:

```console
python -m pip install -e ./ssvep-frequency-test
```

Install `ssvep-bci` or `psychopy-ssvep` in the environment appropriate for
that application. Conda, Poetry, uv, pipx, a standard virtual environment, or
a system Python can all be used; no environment directory name is assumed.

Upload `arduino_light_sensor_sketch/arduino_light_sensor_sketch.ino` to the
Arduino. It samples A0 and emits newline-delimited 10-bit values at 19200 baud.

## Recommended monitor-check profile

Both applications include `frequency-validation.yaml`. It presents one
400 x 400 px square flush with the bottom-right corner at 8.25, 9.75, 12.75,
and 14.25 Hz for ten seconds each. Keep the photosensor fixed on the square for
the whole run.

Run the Qt/OpenGL application directly:

```console
python -m ssvep_bci run --config frequency-validation --participant MONITOR_TEST
```

Or run the PsychoPy application:

```console
python -m psychopy_ssvep run --config frequency-validation --participant MONITOR_TEST
```

Before using the PsychoPy profile, set its `refresh_rate_hz`, `monitor_name`,
and gamma for the display being tested. The bundled defaults target a 60 Hz
monitor with gamma 1.0.

## Record independently

Start the photosensor recorder before starting the SSVEP session. The output
directory can be anywhere:

```powershell
record-photosensor --port COM3 --out ".\measurements\monitor-test"
```

On Linux, the port is commonly `/dev/ttyACM0` or `/dev/ttyUSB0`. Stop the
recorder with Ctrl+C after the experiment finishes. It writes:

- `light_amp.csv`: monotonic time, wall time, and raw sensor value;
- `photosensor_sync.csv`: recorder start, warm-up completion, and end markers.

## Portable two-window workflow

The `ssvep-frequency-validation` command is implemented in Python and works the
same way in PowerShell, Command Prompt, Bash, and zsh. Run these commands from
the `BCI` directory. If the package is not installed, replace
`ssvep-frequency-validation` with
`python ssvep-frequency-test/frequency_validation.py`.

1. In terminal 1, start recording. Use the serial port for the current OS:

   ```console
   ssvep-frequency-validation record --port COM3
   ssvep-frequency-validation record --port /dev/ttyACM0
   ```

2. When samples are flowing, run one presentation app in terminal 2:

   ```console
   ssvep-frequency-validation run ssvep-bci --participant MONITOR_TEST
   ssvep-frequency-validation run psychopy-ssvep --participant MONITOR_TEST
   ```

3. After the presentation closes, return to terminal 1 and press Ctrl+C. Then
   analyze the matching app:

   ```console
   ssvep-frequency-validation analyze ssvep-bci
   ssvep-frequency-validation analyze psychopy-ssvep
   ```

`record` creates a timestamped folder under `./measurements`. `run` writes to
`./<application>/sessions`. `analyze` selects the newest compatible folders,
prints both selections, and writes results into the session's
`photosensor-check` directory.

If the presentation application is installed in a different environment, pass
that environment's Python executable without changing the coordinator's own
environment:

```console
ssvep-frequency-validation run psychopy-ssvep --python /opt/psychopy/bin/python
ssvep-frequency-validation run ssvep-bci --python C:/path/to/env/Scripts/python.exe
```

Custom data layouts are supported with `--workspace`, `--measurement-root`,
and `--session-root`. For example:

```console
ssvep-frequency-validation analyze ssvep-bci --measurement-root /data/light --session-root /data/sessions
```

## Analyze an explicit session

```console
run-photosensor-check --session ./psychopy-ssvep/sessions/<session-folder> --light ./measurements/monitor-test/light_amp.csv
```

The same command accepts an `ssvep-bci` session folder.

## Analyze the latest session

Point the tool at exactly one application's session root:

```console
run-photosensor-check --latest --session-root ./ssvep-bci/sessions --light ./measurements/monitor-test/light_amp.csv
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

These are post-session analysis files. They are deliberately not inserted into
the recording manifest or checksum list. Re-running the check replaces these
known result files and leaves all other session files untouched.

`summary.csv` reports target and detected frequency, frequency error, PASS/FAIL,
signal range, sample count, duration, frame-skip estimate, and harmonic checks
for each presentation. The default pass tolerance is 0.25 Hz and can be changed
with `--tolerance-hz`. The default 70 ms serial-latency correction can be changed
with `--latency`.

## Practical notes

- Run the recorder and experiment on the same computer so their monotonic
  timestamps are directly comparable.
- Keep the sensor over only one stimulus. The validation profiles intentionally
  show one bottom-right square at a time.
- Ten-second validation windows provide 0.1 Hz FFT resolution.
- Low contrast means the sensor is saturated, poorly positioned, or seeing too
  much ambient light.
- Frequency identity and frame timing are separate checks: inspect both the FFT
  and time-domain reports.
