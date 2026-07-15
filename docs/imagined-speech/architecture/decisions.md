# Architecture Decision Record

This is a consolidated record of significant decisions implemented through
roadmap Milestones 1–3. Each entry states the decision, why it was made, and
its consequences. It is descriptive of current code; future changes should
append or supersede decisions rather than silently rewriting their history.

## AD-001 — Use versioned, strict, typed configuration

**Decision.** Parse YAML into Pydantic models that reject unknown keys, validate
cross-field rules, and carry schema version 1.

**Rationale.** Research runs should fail before recording when a protocol key
is misspelled or contradictory. Typed models also provide a stable contract to
planning, UI, acquisition, and persistence.

**Consequences.** Configuration errors are early and explicit, but schema
changes require migrations/version handling. Frozen models are read-only by
convention; nested dictionaries are not deeply immutable.

## AD-002 — Separate experiment configuration from device profiles

**Decision.** Store protocol/presentation design in experiment YAML and
hardware/montage/connection settings in a referenced device YAML.

**Rationale.** The same scientific protocol must run against native synthetic,
BrainFlow synthetic/replay/Cyton, and LSL sources.

**Consequences.** Device substitution does not require duplicating protocol
design. The current plan fingerprint does not include device-file contents, so
the session snapshot—not `plan_id`—is the authoritative device record.

## AD-003 — Resolve paths relative to the declaring file

**Decision.** Experiment assets/device/output paths are relative to the
experiment YAML; replay paths are relative to the device YAML.

**Rationale.** Configurations should behave identically from the CLI, tests,
IDE, or installed entry point regardless of working directory.

**Consequences.** YAML bundles are portable as directory trees. Moving a YAML
without its relative resources can invalidate it, which validation exposes.

## AD-004 — Compile configuration into a persisted immutable plan

**Decision.** Convert configuration to a complete `SessionPlan` before runtime
and persist that exact plan.

**Rationale.** Runtime components should execute known work, not perform
randomization or reinterpret scientific intent while recording.

**Consequences.** Preview, engine, and validator share one execution contract.
Plan-schema changes must be versioned, and dynamic adaptive protocols would
need explicit plan-amendment events rather than hidden mutation.

## AD-005 — Make randomization deterministic and block-aware

**Decision.** Use a configured seed, balance stimulus repetitions across
blocks, and shuffle practice/each block with separate seed namespaces.

**Rationale.** The same configuration must reproduce the same ordering while
maintaining block balance.

**Consequences.** Tests can compare exact plans and recorded sessions retain
their ordering. The algorithm does not yet enforce adjacency/category
constraints; adding those changes the compiler contract.

## AD-006 — Keep the protocol engine independent of Qt and hardware

**Decision.** The engine consumes a plan, configuration, clock, and event sink;
it imports neither Qt nor acquisition adapters.

**Rationale.** Scientific transition logic must be testable headlessly and
must behave the same with UI, virtual simulation, or real acquisition.

**Consequences.** UI and acquisition communicate through `ViewState` and
events. The CLI must explicitly wire components, which is more verbose but
makes dependencies visible.

## AD-007 — Use monotonic time for durations and UTC for audit time

**Decision.** Every event records both a monotonic timestamp and timezone-aware
UTC, while deadlines use only monotonic time.

**Rationale.** Wall clocks can jump; monotonic clocks cannot be mapped easily
to human records. Both meanings are needed.

**Consequences.** Duration calculations remain stable and records are
human-correlatable. Cross-device synchronization still requires source
timestamps and backend-specific correction/alignment.

## AD-008 — Support explicit real and virtual clocks

**Decision.** Inject a small clock contract and provide `RealClock` and
manually advanced `VirtualClock`.

**Rationale.** A 40–60 minute protocol must be testable in seconds without
changing transition code.

**Consequences.** Native synthetic sessions can be deterministic and fast.
External/hardware sources require real time because their clocks cannot jump
with the protocol clock.

## AD-009 — Represent all semantic boundaries as structured events

**Decision.** Emit typed, sequenced `ProtocolEvent`s for lifecycle, hierarchy,
phase, stimulus, operator, abort, and failure boundaries.

**Rationale.** Numeric hardware markers alone cannot carry enough context to
reconstruct a session or explain recovery/failure.

**Consequences.** `events.jsonl` is the source of truth and can drive
validation, timelines, synchronization, replay, and future offline analysis.
Event schema evolution must remain backward compatible or versioned.

## AD-010 — Fan out events through a minimal synchronous sink interface

**Decision.** Use `EventSink.emit` and `CompositeEventSink` rather than a global
event bus/framework.

**Rationale.** Current consumers need deterministic order and low complexity;
tests need easy in-memory substitution.

**Consequences.** Failures propagate visibly and ordering is clear. Slow future
consumers must enqueue their own work because synchronous processing can delay
the engine.

## AD-011 — Expose a presentation model instead of engine internals

**Decision.** The subject UI renders immutable `ViewState` snapshots and never
owns its own phase machine.

**Rationale.** Competing UI timers/state commonly cause displayed stimuli and
recorded markers to diverge.

**Consequences.** Engine state is authoritative, alternative UIs can reuse it,
and diagnostics stay out of the subject contract. UI refresh frequency affects
visual countdown smoothness, not phase order.

## AD-012 — Treat the CLI as the composition and lifecycle root

**Decision.** Build the per-session object graph and order startup/shutdown in
`cli._execute_session`.

**Rationale.** There is currently one local application process, so a
dependency-injection framework would add indirection without solving a present
problem.

**Consequences.** The lifecycle is readable and testable at module boundaries.
A future experimenter application may extract an application service from the
CLI when multiple front ends need identical orchestration.

## AD-013 — Hide source-specific behavior behind `AcquisitionBackend`

**Decision.** Normalize source lifecycle and reads to a small protocol and
`SampleBatch`.

**Rationale.** BrainFlow matrices, LSL chunks, and deterministic generated data
should not change engine or recorder contracts.

**Consequences.** New backends can be added in the factory. The common contract
must remain intentionally small; richer source metadata lives in the backend
metadata mapping.

## AD-014 — Provide two hardware-free acquisition paths

**Decision.** Implement both native in-process synthetic data and a standalone
synthetic LSL publisher, plus BrainFlow's synthetic board.

**Rationale.** They test different layers: native synthetic tests the
application deterministically; LSL publisher tests discovery/transport/timing;
BrainFlow synthetic tests the production adapter API.

**Consequences.** Developers can choose speed or integration realism. LSL and
BrainFlow runs use real time and optional dependencies; only native synthetic
supports virtual time and local embedded markers without another process.

## AD-015 — Decouple source reads from raw disk writes

**Decision.** Use a reader thread, bounded batch queue, and writer thread for
real-time sources; keep the UI thread out of both operations.

**Rationale.** Disk latency and Qt work should not block acquisition reads.

**Consequences.** Queue overruns are possible and explicitly recorded. Thread
lifecycle and locking add complexity. Virtual synthetic uses explicit reads
because its clock jumps, while retaining the writer queue.

## AD-016 — Persist source data without destructive online processing

**Decision.** Write every value returned by the backend with timing/receipt
columns; do not filter, re-reference, resample, or QC-clean raw EEG.

**Rationale.** Research data must remain auditable and future algorithms must
be comparable against the same raw input.

**Consequences.** Files may be larger/noisier and downstream analysis must
apply explicit derivatives. BrainFlow preserves all board rows; LSL preserves
all stream channels.

## AD-017 — Keep structured events authoritative while auditing native markers

**Decision.** Request device marker insertion when supported, but always write
a per-event acquisition marker sidecar.

**Rationale.** LSL inlets cannot inject into upstream data, device insertion
can fail, and embedded values carry less semantic context.

**Consequences.** All backends share an auditable alignment record. Analyses
must explicitly align event/source clocks and should not assume the marker is
embedded at the exact event timestamp.

## AD-018 — Use a self-contained, versioned session directory

**Decision.** Snapshot inputs/plan, append events/raw/health, declare artifacts
in a manifest, and checksum final packages.

**Rationale.** Recordings should be portable and independently inspectable
without a live database or original working tree.

**Consequences.** Copying one directory transfers a session and checksums
detect later corruption. External asset bytes are not yet copied/hashed, and
checksums are integrity checks rather than signatures or encryption.

## AD-019 — Finalize after acquisition, not at the terminal engine event

**Decision.** Disable `SessionWriter` auto-finalization in the composed runtime;
stop acquisition, register its artifacts, then finalize once in the CLI's
`finally` path.

**Rationale.** Post-roll, final buffers, health records, and metadata are
created after the engine becomes terminal.

**Consequences.** Checksums cover the complete artifact set. Lifecycle ordering
is centralized in the CLI and must remain correct when new artifacts are added.

## AD-020 — Preserve failed, aborted, and incomplete sessions

**Decision.** Convert failures to terminal events/status when possible and
validate partial packages with status-aware rules instead of deleting them.

**Rationale.** Partial EEG and failure context are scientifically and
operationally useful; silent loss is worse than an explicitly incomplete run.

**Consequences.** Offline tools must respect status and cannot assume every
trial is complete. Hard process termination can still leave an in-progress
package without checksums.

## AD-021 — Validate by reconstructing against the persisted plan

**Decision.** Parse typed snapshots/events, verify identities/checksums, and
check structural event order and acquired artifact consistency.

**Rationale.** File existence and marker counts alone do not prove that a
session executed coherently.

**Consequences.** Corruption and incomplete hierarchy are caught early.
Validation currently proves structural integrity, not signal quality or
physical marker latency.

## AD-022 — Keep heavy capabilities optional and lazily imported

**Decision.** Core installation includes Pydantic/PyYAML; Qt and
BrainFlow/LSL/scientific packages are extras, and backend imports occur only on
use.

**Rationale.** Configuration/planning work and CI should not require GUI or EEG
native libraries.

**Consequences.** Errors clearly request the relevant extra at runtime. Some
integration tests are conditional on optional packages, so release validation
must also run with the full extras installed.

## AD-023 — Introduce a GUI-independent session runtime

**Decision.** Put per-session composition, pre/post roll, command execution,
finalization, and validation in `SessionRuntime`; keep Qt layout in
`ExperimenterWindow`.

**Rationale.** Experimenter controls need one owner for engine, acquisition,
and persistence lifecycle, but that owner should remain testable without Qt.

**Consequences.** GUI and tests share command/finalization behavior. Headless
CLI composition remains separate for now, so future consolidation may extract
more shared lifecycle code.

## AD-024 — Run subject and experimenter views in one process with one driver

**Decision.** Open separate Qt windows/displays, but let the experimenter
runtime drive engine ticks while the subject window only renders `ViewState`.

**Rationale.** A second state/timer owner could make displayed phases diverge
from recorded events; separate processes would require a transport protocol not
needed for the local two-display milestone.

**Consequences.** Diagnostics never enter the subject widget contract and both
windows share exact engine state. A GUI-process crash affects both displays,
though recorder finalization is attempted through controlled exception/close
paths.

## AD-025 — Audit command requests separately from protocol effects

**Decision.** Persist a typed `OperatorCommandRecord` for every experimenter
request, accepted or rejected, while accepted effects also remain protocol
events.

**Rationale.** Rejected commands have no engine transition but are still
important operational evidence. Events alone cannot represent state-before,
reason, note, and resulting state consistently.

**Consequences.** `operator-actions.jsonl` contains typed command records plus
legacy action-event copies and its validator understands both. Offline tools
must distinguish entries by `record_type`.

## AD-026 — Implement recovery by superseding and appending attempts

**Decision.** Close active phase/trial scopes with `outcome: superseded`, emit a
repeat event, and insert new actions derived from the persisted plan with
incremented attempts.

**Rationale.** Recovery must never delete earlier EEG/events, and validators
must be able to distinguish final completed work from abandoned attempts.

**Consequences.** Session reconstruction supports plan-position resets and
attempt sequences. Repeat is currently limited to an active block trial; a
post-block repeat requires a future explicit plan-amendment model.

## AD-027 — Render live EEG from a bounded copied snapshot

**Decision.** Keep a five-second ring of recently written source rows in the
recorder and expose immutable snapshots to the experimenter UI.

**Rationale.** Live traces and channel reception should not reread the growing
CSV, block acquisition, or let GUI code touch source buffers.

**Consequences.** Monitoring has bounded memory and can lag the source by the
writer queue. Its `receiving/flat/no data` labels are operational indicators,
not signal-quality results; QC remains Milestone 5.

## Deferred decisions

The following remain open because their modules are not implemented: the QC
queue/backpressure/result schema implementation; binary long-term raw format;
session loader and epoch index APIs; schema migration tooling; and data
governance/encryption/pseudonymization policy.
