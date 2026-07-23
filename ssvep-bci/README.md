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
