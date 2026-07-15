# Imagined-Speech Architecture Guide

This guide documents the architecture implemented through Milestones 1–3 of
the [roadmap](../roadmap.md). It explains the current code, the reasons behind
its boundaries, and the flow of configuration, protocol state, events, EEG
samples, and persisted artifacts.

The [experiment plan](../experiment-plan.md) and roadmap also describe future
experimenter UI, online QC, offline analysis, and recovery features. Those
components are not presented here as if they already exist. The current
runtime includes strict configuration, deterministic planning, a protocol
engine, subject UI, synthetic/BrainFlow/LSL acquisition, raw recording, and
session validation.

## Reading order

1. [System overview and runtime data flow](system-overview.md) — component
   boundaries, composition, startup, normal completion, and failure paths.
2. [Configuration and deterministic planning](configuration-and-planning.md) —
   YAML schemas, validation, path resolution, plan compilation, and
   reproducibility.
3. [Protocol engine, events, and subject UI](engine-events-and-ui.md) — clocks,
   state transitions, event markers, view state, headless runners, and Qt.
4. [Acquisition and raw recording](acquisition-and-recording.md) — backend
   contract, synthetic/BrainFlow/LSL behavior, concurrency, timestamps,
   markers, and health records.
5. [Session packages and validation](session-packages-and-validation.md) —
   append-only persistence, manifests, checksums, reconstruction, and partial
   session handling.
6. [Architecture decision record](decisions.md) — the significant choices made
   so far, their rationale, consequences, and known limitations.

## Source map

| Concern | Primary module |
|---|---|
| CLI composition and lifecycle | [`cli.py`](../../../imagined-speech/imagined_speech/cli.py) |
| Configuration schemas/loading | [`config.py`](../../../imagined-speech/imagined_speech/config.py) |
| Deterministic plan compilation | [`plan.py`](../../../imagined-speech/imagined_speech/plan.py) |
| Protocol events and sinks | [`events.py`](../../../imagined-speech/imagined_speech/events.py) |
| Clock-driven state machine | [`engine.py`](../../../imagined-speech/imagined_speech/engine.py) |
| Headless execution | [`simulation.py`](../../../imagined-speech/imagined_speech/simulation.py) |
| Subject-facing Qt UI | [`subject_ui.py`](../../../imagined-speech/imagined_speech/subject_ui.py) |
| Acquisition interfaces/adapters | [`acquisition/`](../../../imagined-speech/imagined_speech/acquisition/) |
| Session persistence/validation | [`session.py`](../../../imagined-speech/imagined_speech/session.py) |

## Architectural invariants

The implementation is organized around several invariants:

- Configuration is validated before any session directory or hardware
  connection is created.
- A validated configuration is compiled once into a deterministic plan; the
  engine executes that plan rather than making random choices at runtime.
- The engine owns protocol time and state. The UI renders `ViewState` and does
  not maintain an independent phase state machine.
- `events.jsonl` is the authoritative semantic timeline. Embedded device
  markers and the acquisition marker sidecar are synchronization aids, not a
  replacement for structured events.
- Acquisition and raw writing continue independently of UI rendering. EEG is
  saved as received and is not modified by protocol or future QC code.
- Session records are append-only while running and finalized with declared
  artifacts and checksums. Failed and aborted sessions remain inspectable.
- Hardware-specific libraries are behind a small acquisition contract and are
  imported lazily, keeping configuration and protocol work hardware-free.
