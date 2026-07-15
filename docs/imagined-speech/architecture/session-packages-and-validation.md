# Session Packages and Validation

## Package as the persistence boundary

Each run creates a new directory named with UTC creation time, participant ID,
and a UUID prefix. The complete directory is the unit passed to validation and,
in future milestones, offline review and analysis.

The package avoids a mutable database dependency and keeps scientific inputs,
events, raw samples, and integrity information together. Files written during a
run are append-only streams or snapshots; finalization adds declarations and
checksums rather than rewriting scientific records.

## Artifact inventory

| Artifact | Role |
|---|---|
| `manifest.json` | Session identity, schema/software version, status, plan/config identity, declared artifacts |
| `experiment-config.yaml` | Validated experiment snapshot |
| `device-profile.yaml` | Validated device/montage snapshot with recognized secret fields redacted |
| `session-plan.json` | Exact deterministic plan executed by the engine |
| `events.jsonl` | Authoritative ordered semantic event timeline |
| `operator-actions.jsonl` | Subset of pause/resume/abort/failure events |
| `eeg_raw.csv` | Continuous source rows and timing columns |
| `acquisition-markers.jsonl` | Per-event backend marker request/embedding audit |
| `acquisition-health.jsonl` | Append-only acquisition lifecycle and error records |
| `acquisition-metadata.json` | Final source/channel/rate/count/error summary |
| `checksums.sha256` | SHA-256 of every declared artifact other than the checksum file itself |

Acquisition files are registered only after the recorder has stopped. This is
why the CLI constructs `SessionWriter` with `auto_finalize=False`: a terminal
engine event must not finalize checksums before the recorder writes metadata
and closes raw output.

## Creation and finalization

`SessionWriter` validates the participant ID and verifies that the plan's
configuration hash matches the resolved experiment. It then creates a unique
directory, opens the two event logs, writes snapshots, and atomically writes an
`in_progress` manifest.

Each event is serialized and flushed immediately to `events.jsonl`. Action
events are also appended to `operator-actions.jsonl`. Immediate flush favors
recoverability and auditability over maximum event-write throughput; event
volume is small compared with EEG data.

At finalization the writer:

1. closes event/action streams;
2. atomically replaces the manifest with its terminal status and finalized
   timestamp;
3. hashes the registered artifact set;
4. writes `checksums.sha256` and closes itself against further writes.

Artifact registration rejects absolute paths and parent traversal, ensuring a
manifest cannot accidentally claim files outside the session directory.

## Status model

Supported manifest statuses are:

- `in_progress` — active or interrupted before controlled finalization;
- `complete` — engine completed and acquisition was finalized;
- `aborted` — controlled subject/operator abort;
- `failed` — structured runtime/acquisition failure;
- `incomplete` — lifecycle ended without one of the explicit terminal engine
  states.

Partial data is valuable research/audit evidence. Aborted and failed packages
are not deleted, and validation applies status-aware completeness rules.

## Secret handling

Before writing `device-profile.yaml`, connection keys containing `password`,
`secret`, `token`, `api_key`, or `credential` are replaced with `[REDACTED]`.
Serial ports, IP addresses, replay file paths, and similar non-secret connection
metadata remain in the snapshot because they contribute to reproducibility.

This is key-name-based redaction, not a general secret scanner. New connection
fields must use recognizable names or extend the redaction policy.

## Validation pipeline

`validate_session` is read-only. It performs layered checks:

1. Confirm the session directory and required core artifacts exist.
2. Parse the manifest and accept only known schema/status values.
3. Verify final checksums (or warn when an `in_progress` package legitimately
   lacks them).
4. Parse experiment/device snapshots and `SessionPlan` through the same typed
   schemas used by the runtime.
5. Recompute the experiment fingerprint and compare it with plan and manifest;
   compare the manifest plan ID with the snapshot.
6. Parse every event as `ProtocolEvent`, enforce sequence/session identity, and
   reconstruct plan structure.
7. Validate acquisition artifacts, raw header/width/index/count, markers, and
   health JSON.
8. Return a `SessionValidationReport` with status and event/trial/phase/sample
   counts plus warnings.

## Structural reconstruction

Event validation does not merely count lines. It compares the timeline with the
saved plan and enforces the expected nesting/order of rest, break, block, trial,
and phase boundaries, including stimulus presentation. For complete sessions,
every planned trial and phase must close. Aborted/failed/incomplete sessions may
end early but their recorded prefix must still be structurally coherent.

This makes the persisted plan the validation oracle. Offline consumers do not
need to reconstruct randomization from a seed or infer phase identity from
numeric markers.

## Acquisition validation

If no acquisition artifacts are present or declared, the validator returns
zero samples for legacy/protocol-only packages. If acquisition exists:

- a complete session requires all four acquisition artifacts to be present and
  declared;
- partial sets are tolerated only for failed, incomplete, or in-progress
  sessions, and present files must still be declared;
- CSV headers must exactly match metadata channel names;
- every row must have the expected width and contiguous sample index;
- CSV row count must equal metadata sample count;
- a complete acquired session must have at least one sample;
- each acquisition marker must match an event sequence and code, and complete
  artifact validation requires exactly one marker record per event;
- every acquisition health line must be valid JSON.

Validation currently establishes structural integrity, not scientific signal
quality or sub-millisecond event/sample alignment.

## Integrity guarantees and non-guarantees

Checksums detect modification/corruption after finalization. Snapshot hashes
connect the experiment configuration to the compiled plan. Unique session IDs
connect events to their package.

The current format does not provide digital signatures, encryption,
participant pseudonym management, transactional durability across power loss,
or content hashes for external stimulus assets. Those concerns require an
explicit data-governance/security design rather than being inferred from
SHA-256 checksums.

## Evolution rules

Future artifacts such as QC results, epoch indexes, and derivatives should be
registered in the manifest and checksummed, but must not replace raw EEG or
core events. A format change that alters interpretation must increment the
relevant schema version and preserve a loader/validator path for version 1.
