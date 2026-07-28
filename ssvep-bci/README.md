# SSVEP BCI

Independent, configuration-driven PySide6 application for frame-timed SSVEP
presentation, continuous EEG acquisition, reconstructable session recording,
and optional live FBCCA processing. It is a parallel rewrite: it does not import
or wrap `SSVEP-Interface`.

## Install in a virtual environment

The following commands assume PowerShell is open in the repository's `BCI`
directory and place the virtual environment at `BCI\.venv`:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".\ssvep-bci[all]"
```

If the environment already exists, only activate it and install the package.
An activated prompt normally begins with `(.venv)`. Confirm which interpreter
is active with:

```powershell
python -c "import os, sys; print(os.environ.get('VIRTUAL_ENV')); print(sys.executable)"
```

If PowerShell prevents `Activate.ps1` from running, activation is optional. Use
the environment's interpreter explicitly:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".\ssvep-bci[all]"
.\.venv\Scripts\python.exe -m ssvep_bci validate
.\.venv\Scripts\python.exe -m ssvep_bci run --participant TEST001 --windowed
```

When working from inside the `ssvep-bci` directory, the equivalent editable
installation command is:

```powershell
python -m pip install -e ".[all]"
```

Deactivate an environment that was activated in PowerShell with:

```powershell
deactivate
```

Do not run `deactivate.bat` from PowerShell: a batch file cannot change its
parent PowerShell process.

### Linux (Ubuntu 26.04 or comparable)

On a typical Ubuntu installation, install Python's venv and pip support first:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

From the repository's `BCI` directory, create and activate the environment,
then install the application:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "./ssvep-bci[all]"
```

Confirm that the shell is using the environment:

```bash
python -c "import os, sys; print(os.environ.get('VIRTUAL_ENV')); print(sys.executable)"
```

Run the application using either the console command or module form:

```bash
ssvep-bci validate
ssvep-bci run --participant TEST001 --windowed
python -m ssvep_bci run --participant TEST001 --windowed
```

Full-screen, custom-config, session-validation, and test examples use normal
Linux paths:

```bash
ssvep-bci run --participant P012 --session-label baseline
ssvep-bci run --config "./configs/lsl-session.yaml" --participant P012
ssvep-bci validate-session "./ssvep-bci/sessions/20260723T120000Z_TEST001_abcd1234"
python -m pytest ./ssvep-bci/tests
```

Activation is optional on Linux as well. The equivalent explicit-interpreter
form is:

```bash
./.venv/bin/python -m pip install -e "./ssvep-bci[all]"
./.venv/bin/python -m ssvep_bci validate
./.venv/bin/python -m ssvep_bci run --participant TEST001 --windowed
```

Deactivate the active Bash venv with:

```bash
deactivate
```

For BrainFlow serial hardware, the user must have permission to open the board's
device file. On Ubuntu this commonly means membership in `dialout`; log out and
back in after changing group membership:

```bash
sudo usermod -aG dialout "$USER"
```

## Session spectrogram notebook

The optional `analysis` extra installs Jupyter, its Python kernel, pandas, and
matplotlib in addition to the project's core NumPy, SciPy, scikit-learn, and
PyYAML dependencies. From the repository's `BCI` directory:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\ssvep-bci[analysis]"
jupyter lab ".\ssvep-bci\notebooks\session_spectrograms.ipynb"
```

The equivalent Linux commands are:

```bash
source .venv/bin/activate
python -m pip install -e "./ssvep-bci[analysis]"
jupyter lab "./ssvep-bci/notebooks/session_spectrograms.ipynb"
```

Run Jupyter from the repository's `BCI` directory so the notebook's default
relative session path resolves directly. To analyze another compatible
recording, change `SESSION_PATH` in the notebook's configuration cell. For
automatic discovery, set `SESSION_PATH = None` and set `SESSION_ROOT` to a
directory containing session folders; the newest compatible folder name is
selected.

## CLI names

Installation creates the `ssvep-bci` console command. The importable Python
package uses an underscore, so both of these forms are equivalent:

```powershell
ssvep-bci validate
python -m ssvep_bci validate
```

`python -m ssvep-bci` is invalid because Python module names cannot contain a
hyphen. The `python -m ssvep_bci` form is useful when a package is installed but
its console-script directory is not on `PATH`.

## Command examples

Show the available commands or command-specific options:

```powershell
ssvep-bci --help
ssvep-bci run --help
ssvep-bci validate-session --help
```

Validate the bundled experiment and synthetic device profile without opening
the UI or creating a session:

```powershell
ssvep-bci validate
```

Validate a custom experiment before using hardware:

```powershell
ssvep-bci validate --config ".\configs\my-experiment.yaml"
```

Run the bundled deterministic synthetic experiment in a resizable window:

```powershell
ssvep-bci run --participant TEST001 --windowed
```

Run full-screen using the configured screen and add a session label:

```powershell
ssvep-bci run --participant P012 --session-label baseline
```

Run a custom experiment in full-screen or windowed mode:

```powershell
ssvep-bci run --config ".\configs\cyton-session.yaml" --participant P012
ssvep-bci run --config ".\configs\lsl-session.yaml" --participant P012 --windowed
```

The repository includes a four-frequency collection profile. It currently uses
the synthetic device profile for a safe timing/UI dry run; change
`device_profile` to `../devices/cyton.yaml` in a copied experiment config for a
real BrainFlow/Cyton recording.
It mirrors the legacy four-stimulus timing: 8.25, 9.75, 12.75, and 14.25 Hz
flash simultaneously in a 2×2 grid for each 5-second stimulus period. A
5-second red outline cues the target to attend before each period. The target
order is deterministically shuffled in each of five complete sets, followed by
a 15-second set break. This produces 25 seconds of attended flashing data per
frequency and approximately 4 minutes 46 seconds of total acquisition time.
Validate it before connecting the board, then provide the participant ID:

```powershell
ssvep-bci validate --config ".\src\ssvep_bci\resources\configs\four-frequency-collection.yaml"
ssvep-bci run --config ".\src\ssvep_bci\resources\configs\four-frequency-collection.yaml" --participant P012 --session-label four-frequency
```

The profile uses `serial_port: auto`, which requires exactly one detected
FTDI/OpenBCI serial device. Set the explicit port in a copied device profile
when more than one serial device is connected. Processing is disabled in this
collection profile so the session contains continuous raw EEG plus structured
stimulus onset/offset events for offline analysis.

`protocol.stimulus_sequence` is backward-compatible with the original
single-stimulus protocol: each listed ID becomes one trial, and
`protocol.repetitions` repeats the complete sequence. The current UI presents
the IDs in `protocol.simultaneous_stimulus_ids` together while the current
sequence item identifies the attended target and marker label.
`protocol.sequence_break_seconds` adds a break after a complete sequence
without inserting that long break between individual frequencies.

A second bundled profile presents one centered circle for exactly four minutes
of stimulus time. Each of the four frequencies is shown 12 times for 5 seconds;
the order is deterministically randomized within each balanced four-frequency
block. It includes a 5-second pause before the first presentation and a
3-second blank interval between presentations, making the complete planned
protocol 6 minutes 26 seconds:

```powershell
ssvep-bci validate --config ".\src\ssvep_bci\resources\configs\four-frequency-single-circle-4min.yaml"
ssvep-bci run --config ".\src\ssvep_bci\resources\configs\four-frequency-single-circle-4min.yaml" --participant P012 --session-label single-circle-4min
```

This profile uses the synthetic device for dry runs. Change `device_profile` to
`../devices/cyton.yaml` in a copied config for BrainFlow/Cyton acquisition.

The dual-square profile presents two 200×200 px squares on every trial. The
11.75 Hz target is balanced between the left and right positions while the
opposite square uses either 8.75 or 13.75 Hz. Each of 12 blocks contains all
four side/frequency conditions once in a deterministic order controlled by
`random_seed`. A three-second cue shows both dark squares with a red border
around the target; during the four-second stimulation both squares flash and
the border is removed.

Validate the profile, then run a windowed synthetic dry run:

```powershell
ssvep-bci validate --config ".\src\ssvep_bci\resources\configs\three-frequency-dual-square-4sec-4min.yaml"
ssvep-bci run --config ".\src\ssvep_bci\resources\configs\three-frequency-dual-square-4sec-4min.yaml" --participant TEST001 --session-label dual-square --windowed
```

For acquisition hardware, copy the profile and change `device_profile` to the
appropriate device YAML. `dual_stimulus.horizontal_layout: equal_gaps` computes
equal left-edge, inter-square, and right-edge gaps from the actual viewport.
To use fixed normalized centers instead, set `horizontal_layout: manual` and
provide both `left_center_x` and `right_center_x`.

Validate a completed, aborted, failed, or incomplete session folder:

```powershell
ssvep-bci validate-session ".\ssvep-bci\sessions\20260723T120000Z_TEST001_abcd1234"
```

If the current directory is `ssvep-bci`, omit the leading project directory:

```powershell
ssvep-bci validate-session ".\sessions\20260723T120000Z_TEST001_abcd1234"
```

Run the project tests from the `ssvep-bci` directory:

```powershell
python -m pytest
python -m pytest tests\test_dsp.py
python -m pytest tests\test_coordinator.py -v
```

The bundled default uses deterministic synthetic EEG and leaves live DSP
disabled. A custom experiment selects hardware through `device_profile`. Set
`processing.enabled: true` to run the known 60 Hz notch, legacy ten-band filter
bank, and FBCCA path on a background worker. If DSP results are mandatory, also
set `processing.required: true`; an unprocessed window will then fail the
session rather than only being recorded diagnostically.

## Runtime boundaries

The first iteration intentionally uses no application sockets. The Qt thread
sends explicit commands to `ProtocolRuntime` and receives immutable
`ViewState` scenes. Acquisition, raw CSV writing, and DSP each have an owned
background thread and bounded queue. `DspTransport`, `AcquisitionBackend`, and
`OutputSink` are the replacement seams for a future process/socket transport;
the UI has no dependency on those implementations.

The renderer accepts a collection-based `StimulusScene`, although the current
protocol emits one configured circle or rectangle. Stimulus onset and offset
events are timestamped only after the corresponding OpenGL frame swap. The
Fusion style, full Qt palette, stylesheet, and font are applied explicitly, so
native OS colors are not inherited.

## Device profiles

- `synthetic`: deterministic, marker-responsive signals suitable for tests.
- `brainflow`: prepare/start/drain/insert-marker/stop/release lifecycle,
  including strict Cyton-compatible FTDI auto-detection.
- `lsl`: exactly-one-stream resolution, channel mapping, periodic time
  correction, and conversion to the application's monotonic time domain.

All adapters normalize data to `(samples, channels)` `float64` batches with
source, corrected-source, and aligned-monotonic timestamps. The Qt UI never
accesses a device.

## Session artifacts

Each run creates a unique folder below `output.root_dir` containing:

```text
manifest.json
experiment-config.yaml
device-profile.yaml
session-plan.json
events.jsonl
eeg_raw.csv
acquisition-metadata.json
acquisition-markers.jsonl
acquisition-health.jsonl
processing-results.jsonl      # when DSP is enabled
checksums.sha256              # when configured
```

`eeg_raw.csv` begins with sample index, receipt monotonic time, receipt UTC
time, source time, corrected source time, aligned monotonic time, named EEG
channels, and embedded marker. Protocol timing remains in structured events;
it is never inferred only from CSV row positions. Terminal manifests use
`complete`, `aborted`, `failed`, or `incomplete` semantics.
