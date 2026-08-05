# Imagined Speech — PsychoPy Rewrite

This is the process-isolated PsychoPy rewrite of the imagined-speech EEG
experiment software. The previous Qt-subject implementation remains preserved
in the sibling `imagined-speech` project.

The rewrite provides a PsychoPy subject display, PyQt6 operator console, and
backend-owned session runtime. The package uses a `src` layout and Python 3.11
on Windows.

## Runtime architecture

`run` starts two long-lived local processes and one on-demand process:

1. the backend binds an ephemeral `127.0.0.1` TCP port and owns at most one
   active `SessionRuntime`;
2. the Qt operator connects with the `operator` role;
3. the operator launches PsychoPy with the `subject` role only after a session
   has been configured and the operator selects **Init subject UI**. The subject
   process exits when that session is finalized.

Communication is strict, versioned, unauthenticated JSON Lines. Loopback
binding and the private ephemeral port are the intentional access boundary.
The operator can run consecutive recordings while each receives a fresh
PsychoPy process. The backend serializes all runtime mutations and is the sole owner
of acquisition, protocol state, event/marker persistence, and finalization.

PsychoPy receives only presentation-safe state and resolved asset paths. Each
visible boundary is committed by a correlated `Window.flip()` acknowledgement.
The subject process schedules an already-authorized successor locally, so TCP
latency does not lengthen phases. Pause, repeat, and abort first neutralize the
display. Flip, receipt, marker-attempt, clock-calibration, frame-interval, and
audio-scheduling data remain auditable in the session package.

## Experimenter workflow

The operator window has two scenes. Session setup validates participant and
session identifiers, experiment and device profiles, output location, random
seed, subject-screen overrides, montage, audio readiness, and the compiled
protocol preview before a recording can be created. Protocol control then
keeps recording and protocol state visible while exposing start, pause, resume,
repeat, electrode-adjustment, and abort controls.

Live monitoring panes are registry-backed widgets under
`src/imagined_speech/ui/widgets`; each widget owns one projection and consumes
the typed operator-state snapshot. Pane assignments, splitter layouts, setup
defaults, and window geometry persist in a per-user INI. On entering protocol
control, PsychoPy is not running: the primary action is **Init subject UI**.
After PsychoPy connects and passes timing preflight, it becomes **Start
protocol**. New per-user files are
seeded from the committed `src/imagined_speech/resources/experimenter_ui.ini`
without modifying that repository copy.

## Installation

Create a 64-bit Python 3.11 environment. Although PsychoPy 2026.2.1 advertises
Python 3.12 support, its mandatory Windows `pywinhook` dependency currently
publishes binary wheels only through CPython 3.11. Python 3.12 therefore falls
back to an unsupported SWIG/MSVC source build. Backend-only installation intentionally does
not install PsychoPy:

```powershell
python -m pip install -e .
```

Install the required surfaces separately or together:

```powershell
python -m pip install -e ".[ui]"
python -m pip install -e ".[subject]"       # psychopy==2026.2.1
python -m pip install -e ".[acquisition]"
python -m pip install -e ".[all,dev]"
```

## Commands

```powershell
imagined-speech-psychopy validate
imagined-speech-psychopy preview
imagined-speech-psychopy simulate
imagined-speech-psychopy run
imagined-speech-psychopy run-subject
imagined-speech-psychopy validate-session sessions/<session-directory>
imagined-speech-psychopy publish-lsl-synthetic
```

The bundled schema-2 configurations live under
`src/imagined_speech/resources/configs`. Live commands reject schema-1
configurations with a targeted migration error. `validate-session` continues
to reconstruct historical schema-1 packages.

`simulate` defaults to a deterministic virtual clock and exercises the same
presentation contract with a virtual driver. `run-subject` starts a backend
and PsychoPy without Qt, creates one session from its CLI arguments, and starts
automatically after acquisition readiness and timing preflight.

## Configuration and artifacts

Every live experiment requires `schema_version: 2` and an explicit
`presentation.psychopy` block. It defines monitor/window behavior, text and
asset rendering, frame preflight and drop thresholds, IPC timing tolerance,
acknowledgement timeout, and PTB audio settings. Device profiles remain schema
version 1.

The experimenter display override uses the operating-system display device
name, not the Qt list position. The PsychoPy process resolves that stable name
against its own Pyglet display list and verifies the native window after it is
created. A missing or mismatched display fails subject initialization instead
of silently opening on the primary monitor. Supported window modes are
`FULL_SCREEN`, `CENTER`, and `TOP_LEFT`; the old Qt-only
`PREVIOUS_POSITION` mode is not supported and should be replaced with
`CENTER` in external configurations. `--windowed` selects `CENTER`.

Bundled configurations use PsychoPy's installed `testMonitor` profile. A named
calibration is loaded when it exists; otherwise presentation falls back to
`testMonitor` and records both requested and resolved monitor names in the
session metadata. Create a calibrated PsychoPy monitor profile for hardware
acceptance and production data collection.

Schema-2 session packages add:

- `presentation-metadata.json` for resolved settings, preflight, audio, and
  initial/final clock mappings;
- `presentation-timing.jsonl` for requests, accepted/rejected acknowledgements,
  operator-command lifecycle, protocol state snapshots, neutral gaps, backend
  receive times, rejection details, and transport/marker latency;
- `frame-intervals.csv` for real PsychoPy runs.

`operator-actions.jsonl` also records engine state before and after every
command, including presentation revision/ID, pending control, expected neutral
acknowledgement ID, trial, attempt, and any protocol failure reason.

All declared artifacts are checksummed. Native EEG markers are attempted after
the backend receives a flip acknowledgement; the persisted timing data records
that delay instead of treating marker time as physical display onset.

The current architecture reference is
[PsychoPy Process Architecture](docs/architecture/psychopy-process-architecture.md).
