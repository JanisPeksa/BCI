# Imagined Speech

Configurable research application for FEIS-style imagined-speech EEG
experiments. Milestones 1–4 provide strict configuration, deterministic
protocol planning, simulated execution, a subject-facing Qt display,
synthetic/BrainFlow/LSL acquisition, reconstructable session packages, and a
two-display experimenter workflow with live monitoring and recovery controls.

## Architecture

The [architecture guide](../docs/imagined-speech/architecture/README.md)
documents module responsibilities, runtime and sample flow, persistence, and
the decisions made through Milestone 4:

- [System overview and runtime data flow](../docs/imagined-speech/architecture/system-overview.md)
- [Configuration and deterministic planning](../docs/imagined-speech/architecture/configuration-and-planning.md)
- [Protocol engine, events, and subject UI](../docs/imagined-speech/architecture/engine-events-and-ui.md)
- [Experimenter workflow and recovery](../docs/imagined-speech/architecture/experimenter-workflow.md)
- [Acquisition and raw recording](../docs/imagined-speech/architecture/acquisition-and-recording.md)
- [Session packages and validation](../docs/imagined-speech/architecture/session-packages-and-validation.md)
- [Architecture decision record](../docs/imagined-speech/architecture/decisions.md)

The broader design is described in the
[experiment plan](../docs/imagined-speech/experiment-plan.md) and
[implementation roadmap](../docs/imagined-speech/roadmap.md). Implemented
desktop lifecycle, settings, control-state, and configurable center-workspace
work is described in the
[experimenter desktop follow-up roadmap](../docs/imagined-speech/experimenter-desktop-roadmap.md).

## Setup

From this directory, create or activate a Python 3.11+ environment and install
the full current milestone:

```powershell
python -m pip install -e ".[ui,acquisition,dev]"
```

For configuration, protocol, and UI development without BrainFlow/LSL, use:

```powershell
python -m pip install -e ".[ui,dev]"
```

## Commands

With no configuration argument, commands use the bundled hardware-free smoke
profile.

```powershell
python main.py validate
python main.py preview
python main.py preview --config imagined_speech/resources/configs/imagined_only.yaml
python main.py preview --config imagined_speech/resources/configs/feis_comparable.yaml
python main.py simulate
python main.py run
python main.py run-subject --clock virtual --windowed
python main.py validate-session sessions/<session-directory>
python main.py publish-lsl-synthetic
```

Running `python main.py` without a command prints command help. Configuration
and device paths are resolved relative to the YAML file that contains them.

`simulate` uses the fast virtual clock by default and writes a complete session
package including deterministic synthetic EEG. Use `--clock real` for
wall-clock execution.

`run` opens the experimenter application. **Connect and start recording**
connects the selected source, starts raw recording, and opens the passive
subject display without starting the protocol. The experimenter can position
or maximize that window and then use **Start protocol** to begin pre-roll.
After finalization, **Back to setup / New session** creates another independent
recording without restarting the app.

Setup selects only the subject display; the long-lived experimenter window is
never relocated when recording starts. Its geometry survives application
restarts. Subject geometry and normal/maximized/full-screen state are remembered
separately for each display. `presentation.window_mode` controls opening:
`FULL_SCREEN`, `PREVIOUS_POSITION`, `TOP_LEFT`, or `CENTER`; previous placement
falls back to centered when that display has no saved value. The obsolete
`full_screen` boolean is rejected; configurations must use `window_mode`.

The fixed experimenter header and controls surround a configurable monitoring
workspace with eight nested-splitter layouts. Each pane independently selects
Live EEG, Channel reception, Recent protocol markers, Operator command audit,
or Acquisition and storage health. The Layout picker changes topology and
**Reset monitoring layout** restores the four-pane default. Setup values,
both window placements, layout, pane assignments, and splitter sizes persist in the
per-user `experimenter_ui.ini`; they are never included in session packages.

`run-subject` retains the direct subject-only runner. Use `--clock virtual
--windowed` for fast visual development. Press Escape to abort a subject run
safely.

## Acquisition modes

- `synthetic.yaml` is the default. It is deterministic, requires no external
  process, supports virtual time, and embeds protocol markers in its generated
  stream.
- `brainflow_synthetic.yaml` exercises the same BrainFlow adapter used by
  Cyton/replay without requiring hardware.
- `lsl.yaml` receives an eight-channel EEG stream named
  `imagined-speech-synthetic`. LSL acquisition runs with the real clock.
- `cyton.yaml` and `replay.yaml` use BrainFlow. Configure the serial port or
  replay file before using them.

To exercise LSL without hardware, use two terminals:

```powershell
# Terminal 1
python main.py publish-lsl-synthetic

# Terminal 2
python main.py simulate --clock real --config imagined_speech/resources/configs/smoke_lsl.yaml
```

Recorded packages include `eeg_raw.csv`, acquisition metadata and health logs,
and a marker-alignment sidecar. BrainFlow backends also request marker insertion
in the native board stream; LSL events remain authoritative in the sidecar
because an inlet cannot modify its source stream. BrainFlow's official Python
pattern uses `prepare_session`, `start_stream`, `get_board_data`, marker
insertion, and controlled release; LSL consumers resolve a stream and pull
timestamped chunks through an inlet. See the [BrainFlow examples][brainflow]
and [LSL inlet documentation][lsl-inlet].

[brainflow]: https://brainflow.readthedocs.io/en/stable/Examples.html
[lsl-inlet]: https://labstreaminglayer.readthedocs.io/projects/liblsl/ref/inlet.html
