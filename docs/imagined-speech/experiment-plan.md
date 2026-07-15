# Configurable Imagined-Speech EEG Experiment Platform

## Summary

Build `imagined-speech` as a configurable research application modeled on the FEIS recording procedure, while selectively reusing acquisition and timing patterns from `mind-speech-interface-ssvep`.

The first study targets a balanced phoneme benchmark rather than direct alphabet spelling. FEIS used 160 randomized trials, four 5-second phases, 14 channels at 256 Hz, and approximately 60 minutes per participant. Its subject-dependent SVM performed above chance, while its CNN required more data. [Clayton et al.](https://www.isca-archive.org/interspeech_2020/clayton20_interspeech.pdf)

Support two configurable protocol modes:

- `imagined_only`: REST -> CUE/STIMULUS -> THINKING -> PAUSE
- `feis_comparable`: REST -> STIMULI -> THINKING -> SPEAKING

The overt-speaking phase is optional and primarily serves verification and alignment.

## Architecture and implementation

Refactor `imagined-speech` into modules for experiment configuration, a session/trial state machine, subject and experimenter Qt6 UIs, BrainFlow/LSL acquisition, raw recording and event markers, asynchronous QC workers, and offline replay/analysis.

The protocol engine must be configuration-driven. Configuration defines phase durations, visual/audio presentation, marker codes, vocabulary, randomized trials, blocks, breaks, calibration and practice trials, montage, device profile, and output paths.

Define a stable event model for session, block, trial, and phase boundaries; stimulus identity; pause/resume/repeat/abort; QC warnings; and operator actions. Each event includes monotonic and wall-clock timestamps, identifiers, phase, stimulus label, and source.

Acquisition continues independently of QC. QC never modifies raw EEG. Operator actions such as pause, resume, repeat, refit, and abort must be recorded.

## User interfaces

The subject-facing display is full-screen and provides REST, cue/stimulus, THINKING, PAUSE, and optional SPEAKING displays, optional audio, progress, and recovery states. It must not expose researcher diagnostics.

The experimenter-facing display provides session setup, protocol preview, current phase and progress, live EEG traces, channel state, marker timeline, recording health, QC warnings, and pause/resume/repeat/refit/abort controls. It can export a session summary and QC report.

## Hardware and montage

OpenBCI Cyton is the primary device, with BrainFlow synthetic and replay support for development. Define a documented default 8-channel speech-relevant montage but make montage profiles configurable. Persist channel labels, electrode positions, reference, ground, sampling rate, hardware settings, and contact/impedance information when available for every session.

## QC pipeline

The first QC release is asynchronous, algorithmic, and non-destructive. It checks missing samples, sample-rate deviation, timestamp gaps, buffer overruns, amplitude and variance, flatline and saturation, drift, line-noise power, high-frequency EMG proxy, and epoch outliers. It may also include optional ASR-style burst flags, FASTER-inspired outlier scoring, and Riemannian Potato/RPF SQIs.

FASTER is a precedent for statistical detection of bad channels, trends, noise, eye movement, and EMG artifacts. RPF is designed for online EEG signal-quality monitoring. [FASTER](https://pubmed.ncbi.nlm.nih.gov/20654646/), [Riemannian Potato Field](https://pubmed.ncbi.nlm.nih.gov/30668501/)

All QC methods return a shared `QCResult` containing the method, scope, severity, score, affected channels/time range, explanation, recommended action, and timestamp. Use configurable thresholds and hysteresis. ML quality models are deferred until locally labeled recordings are available.

## Data and analysis outputs

Create a versioned session package containing raw EEG, event markers, configuration snapshot, hardware/montage metadata, subject/session identifiers, QC results, operator actions, an epoch index, optional derivatives, software version, and checksums.

Offline tools must validate sessions, load EEG and markers, extract epochs, visualize data, replay/compare QC methods, and export data for subject-dependent phoneme classification. Start with interpretable features and SVM baselines; introduce CNN comparisons later. Report per-subject phoneme discrimination, confusion matrices, chance baselines, and the effect of QC filtering; do not interpret this as direct spelling capability.

## Session design

The default session fits within 40-60 minutes: headset fit verification, baseline/rest recording, practice, phoneme benchmark blocks, optional overt-speaking verification, breaks, and final rest. All durations, repetitions, blocks, and vocabulary choices are configurable. Show projected duration before recording begins.

## Test and acceptance criteria

Use automated tests for deterministic, hardware-independent behavior such as configuration validation, protocol planning, event ordering, session reconstruction, synthetic/replay acquisition, and QC algorithms. Validate Qt presentation, multimedia, real Cyton acquisition, physical artifact conditions, and two-display operation manually.

The operator verifies both protocol modes; correct subject and experimenter displays; EEG and marker recording; pause/resume/repeat/abort behavior; asynchronous QC while recording continues; useful warnings and logged actions; valid offline session loading; and a practical 40-60 minute total duration.

The acceptance run includes a synthetic/replay dry run, short real-hardware calibration, imagined-only and FEIS-comparable blocks, intentional loose-electrode/movement/muscle-noise conditions, control recovery scenarios, and inspection of saved EEG, markers, metadata, QC events, and operator log.

The application is accepted when a researcher can conduct the complete session without code changes, recognize unacceptable quality, recover or repeat a block, and obtain a complete, interpretable recording package.

## Assumptions

- The initial scientific target is a FEIS-style phoneme benchmark; letter spelling is a later protocol profile.
- Both protocol modes are required from the beginning.
- Raw EEG is never altered by online QC.
- Algorithmic QC is the initial scope; ML QC is a later extension.
- Existing SSVEP code is reused selectively, while `imagined-speech` has its own package and data contracts.
