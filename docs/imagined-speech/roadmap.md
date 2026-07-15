# Imagined-Speech Experiment Platform Roadmap

## Purpose

This roadmap describes the complete implementation of
[`experiment-plan.md`](experiment-plan.md) as a sequence of runnable,
evidence-producing milestones rather than small technical chunks.

Every milestone must leave the application usable and must preserve the
central invariant: raw EEG and previously recorded events are append-only and
never modified by QC or recovery actions.

## Milestone 1 — Protocol Contract and Foundation

Establish the scientific and software contracts before building the runtime.

- Convert the prototype into an `imagined_speech` Python package with
  `main.py` as the single launcher.
- Add Python 3.11 dependency management for PyQt6, BrainFlow, Pydantic,
  PyYAML, NumPy/SciPy, and pytest.
- Define versioned configuration models for:
  - protocol profile, phase durations, stimuli, repetitions, blocks, breaks,
    practice, and random seed;
  - subject-display presentation and optional audio;
  - device, montage, channel labels, reference, ground, and sampling rate;
  - marker codes, QC settings, and output paths.
- Supply:
  - a short, hardware-free smoke configuration;
  - full `imagined_only` and `feis_comparable` configurations;
  - synthetic, replay, and Cyton device profiles.
- Use the 16 FEIS phonemes `/p t k f s ʃ v z ʒ m n ŋ i u æ ɔ/`, ten
  repetitions each, giving 160 trials. The source's isolated "6 phonemes"
  wording is treated as a typo because its table contains 16 and the
  experiment reports 160 trials. [FEIS paper][feis-paper]
- Default full protocol:
  - four blocks of 40 trials;
  - four 5-second phases per trial;
  - four practice trials;
  - 60-second initial rest, final rest, and inter-block breaks;
  - projected runtime of approximately 59 minutes 40 seconds.
- Define a provisional Cyton montage of `F3, F4, C3, C4, T7, T8, P3, P4`,
  with reference and ground recorded separately and the entire profile
  configurable.
- Add launcher commands to validate a configuration and preview its trial
  balance, phase order, assets, and projected duration.

**Complete when:** all supplied configurations validate, the smoke profile
requires no EEG hardware or audio, and both full profiles preview as balanced
40–60 minute sessions.

## Milestone 2 — Reproducible Simulated Experiment

Deliver the first vertical slice: protocol engine, subject display, events,
and session persistence without EEG.

- Compile configuration into an immutable `SessionPlan` containing randomized
  practice and experiment trials, breaks, rests, phases, and stable
  identifiers.
- Use deterministic seeded randomization. Identical configuration and seed
  must produce the same plan.
- Implement canonical phase sequences:
  - `imagined_only`: REST → STIMULUS → THINKING → PAUSE.
  - `feis_comparable`: REST → STIMULUS → THINKING → SPEAKING.
- Build a monotonic-clock state machine independent of Qt timers. Provide
  real-time and fast virtual-clock runners.
- Define `ProtocolEvent` with sequence number, event and marker codes,
  monotonic and UTC timestamps, source, session/block/trial/attempt IDs,
  phase, stimulus, and payload.
- Implement the subject UI:
  - selected-screen full-screen mode plus windowed development mode;
  - REST, stimulus, THINKING, PAUSE, SPEAKING, break, paused, completed, and
    aborted displays;
  - optional image/audio stimuli, countdown, block/trial progress, and neutral
    recovery screens;
  - no researcher diagnostics or controls.
- Drive the UI entirely from protocol state; it must not maintain a competing
  phase timer.
- Write a versioned session package containing:
  - manifest and checksums;
  - configuration snapshot and compiled plan;
  - events and operator-action logs;
  - session status and software version.
- Mark interrupted sessions as incomplete or aborted without deleting their
  partial records.
- Add session validation that reconstructs every block, trial, attempt, and
  phase from events.

**Complete when:** both profiles run visually with a virtual or real clock and
produce deterministic, reconstructable session packages without EEG hardware.

## Milestone 3 — EEG Acquisition and Synchronization

Add continuous raw acquisition without changing the protocol/UI contracts.

- Define an `AcquisitionBackend` interface implemented by BrainFlow synthetic,
  replay, and Cyton backends.
- Run acquisition and writing outside the Qt UI thread.
- Start recording before initial rest and stop only after final rest or
  controlled abort.
- Save unmodified BrainFlow board rows to `eeg_raw.csv`, including EEG, board
  timestamps, package receipt timestamps, and any auxiliary channels.
- Insert protocol markers into the board stream while retaining the
  authoritative structured event log.
- Persist:
  - board ID and connection parameters with secrets removed;
  - effective sampling rate;
  - channel mappings and units;
  - montage, reference, ground, gains, filters, firmware, and contact
    information when available.
- Detect and record acquisition startup failures, dropped data, queue
  overruns, timestamp discontinuities, and controlled shutdown results.
- On UI or protocol failure, attempt to finalize raw recording and mark the
  package `failed`; never silently discard samples.

**Complete when:** synthetic and replay runs save continuous EEG aligned with
protocol events, followed by a short Cyton run demonstrating usable marker
alignment.

## Milestone 4 — Experimenter Workflow and Recovery

Deliver the complete two-display operator experience.

- Add session setup for participant/session identifiers, protocol, device,
  montage, output location, seed, and audio readiness.
- Preview projected duration, trial balance, selected displays, and
  configuration warnings before recording.
- Show:
  - connection and recording state;
  - current phase and remaining time;
  - block/trial progress;
  - recent markers;
  - live EEG traces and per-channel status;
  - storage and acquisition health.
- Implement operator actions:
  - pause freezes the protocol clock while acquisition continues;
  - resume continues the remaining phase;
  - repeat trial/block closes the previous attempt as superseded and appends a
    new attempt without deleting data;
  - refit records a note and pauses the protocol while acquisition continues;
  - abort closes active scopes and finalizes an aborted package.
- Send only presentation state to the subject display; diagnostics and
  controls remain experimenter-only.
- Record every accepted and rejected operator command with source, timestamp,
  reason, and resulting state.

**Complete when:** one researcher can configure, conduct, recover, and finalize
a synthetic two-display session without terminal interaction or code changes.

## Milestone 5 — Core Online QC

Add actionable signal-quality feedback without affecting acquisition.

- Send copied sample windows to asynchronous QC workers.
- Implement configurable checks for:
  - missing samples and timestamp gaps;
  - sample-rate deviation and buffer overruns;
  - amplitude and variance anomalies;
  - flatline, clipping, and saturation;
  - drift;
  - 50/60 Hz line-noise power;
  - high-frequency EMG proxy;
  - trial/epoch outliers.
- Normalize results to `QCResult`: method, scope, severity, score, channels,
  time range, explanation, recommended action, and timestamp.
- Add threshold hysteresis to avoid warning flicker.
- Display warnings and recovery guidance in the experimenter UI.
- Persist all results, including cleared warnings.
- If QC falls behind, drop QC work and emit a health warning; never block
  acquisition or raw-data writing.

**Complete when:** deliberate flatline, loose-contact, movement, and
muscle-noise conditions create useful warnings while raw recording continues
unchanged.

## Milestone 6 — Offline Review and Research Baseline

Make completed and interrupted recordings independently inspectable.

- Build a validator for manifests, checksums, configuration, event nesting,
  marker alignment, sample continuity, and required metadata.
- Load raw EEG, events, QC, and operator actions through a stable
  `SessionLoader`.
- Generate an epoch index without copying or altering raw EEG.
- Provide plots for raw channels, event overlays, spectral summaries, QC
  timelines, and rejected/superseded attempts.
- Support offline reruns of core QC with alternate thresholds.
- Export analysis-ready epochs with participant, stimulus, phase, block,
  attempt, QC, and timing metadata.
- Implement an interpretable subject-dependent baseline:
  - statistical and band-power features;
  - SVM evaluation;
  - class/chance baselines and confusion matrices;
  - results with and without QC filtering.
- Report phoneme discrimination only; do not describe results as spelling
  performance.

**Complete when:** a fresh environment can validate a session package,
reproduce its epochs, inspect quality, and run the baseline analysis without
using the live application.

## Milestone 7 — Experimental QC Extensions

Evaluate advanced methods only after the basic pipeline produces local
recordings.

- Add separately enableable adapters for:
  - FASTER-style statistical scoring;
  - ASR-style burst flags;
  - Riemannian Potato/RPF signal-quality indicators.
- Normalize all methods to `QCResult`.
- Run methods offline first, then permit online use only after runtime and
  false-warning behavior are measured.
- Provide comparison reports for agreement, warning rate, affected
  channels/epochs, runtime, and threshold sensitivity.
- Keep advanced QC outputs in derivatives; they never rewrite raw EEG or core
  events.
- Defer learned/ML quality models until locally labeled examples exist.

**Complete when:** researchers can compare basic and advanced QC on the same
immutable sessions and enable methods independently.

## Milestone 8 — Pilot Validation and Research Release

Validate the whole platform under realistic operating conditions.

- Write operator setup, montage, audio, recovery, shutdown, and
  session-inspection checklists.
- Run:
  - fast synthetic smoke tests;
  - recorded replay sessions;
  - short Cyton calibration;
  - full imagined-only and FEIS-comparable blocks;
  - a complete 40–60 minute pilot.
- Exercise disconnection, missing audio, loose electrodes, movement, muscle
  activity, pause/resume, repeats, refit, abort, application failure, and
  disk/output errors.
- Confirm subject and experimenter displays never expose each other's
  information.
- Inspect raw EEG, markers, configuration, montage metadata, events, QC,
  operator actions, epochs, derivatives, and checksums.
- Freeze schema version 1 only after the pilot packages pass validation and
  can be independently loaded.

**Complete when:** a researcher can run the entire experiment without source
changes, recognize unacceptable quality, recover without losing raw data, and
obtain an interpretable research package.

## Shared Architecture and Interfaces

- `ExperimentConfig` — validated, versioned configuration.
- `SessionPlan` — immutable randomized execution plan.
- `ProtocolEngine` — clock-driven state machine.
- `ProtocolEvent` and `EventSink` — shared event boundary for recording, UI
  timelines, acquisition markers, and replay.
- `AcquisitionBackend` — synthetic/replay/Cyton abstraction.
- `SessionWriter` and `SessionLoader` — only supported persistence boundary.
- `QCMethod` and `QCResult` — common online/offline QC contract.
- Subject UI, experimenter UI, acquisition, recording, and QC communicate
  through typed state/events rather than importing one another's widgets or
  internal state.

## Verification Policy

- Automated tests cover configuration, deterministic planning, state
  transitions, event ordering, session reconstruction, checksums, acquisition
  integration with synthetic/replay data, and QC algorithms.
- Manual tests cover Qt presentation, multimedia, display selection, Cyton
  hardware, physical artifacts, and operator usability.
- Every milestone adds a repeatable smoke command and acceptance checklist.
- CNN classification, learned QC, direct spelling, cloud services, and
  destructive online EEG preprocessing remain outside the initial roadmap.

[feis-paper]: https://www.isca-archive.org/interspeech_2020/clayton20_interspeech.pdf
