# PsychoPy Process Architecture

This document supersedes the Qt subject-window execution described in older
milestone documents. PyQt6 is now operator-only; PsychoPy is the only
subject-facing UI.

On Windows the supported interpreter is 64-bit CPython 3.11. PsychoPy 2026.2.1
allows Python 3.12 at the top-level metadata layer, but its mandatory
`pywinhook` dependency has no CPython 3.12 Windows wheel and otherwise requires
a local SWIG/MSVC native build. Standardizing on 3.11 keeps installation on
published binary wheels.

## Packages

The application uses `imagined-speech/src/imagined_speech` with feature-based
packages: `config`, `planning`, `events`, `runtime`, `acquisition`, `recording`,
`ipc`, `presentation`, and `ui`. `runtime/protocol.py` has no Qt, PsychoPy,
networking, acquisition, or filesystem imports. `runtime/coordinator.py` owns a
single recording lifecycle. PsychoPy imports are lazy and confined to the
presentation package.

## Processes and session lifecycle

The `run` supervisor starts the backend, reads its selected loopback port from
a private bootstrap pipe, and then starts the operator client. The operator
launches a subject client on demand only after a session exists and the
operator selects **Init subject UI**. Clients identify only their role and
protocol/software version; authentication is intentionally absent. One
operator and one subject may connect.

The backend stays alive between recordings but owns zero or one active runtime.
`CreateSession` constructs a fresh runtime and package without requiring or
starting PsychoPy. When the on-demand subject connects, the backend performs
clock calibration, sends `SubjectInit`, waits for display/audio timing
preflight, starts acquisition, and reports `READY`. The primary operator action
then changes from **Init subject UI** to **Start protocol**. The subject process
exits at finalization, so a later recording receives new identifiers, buffers,
callbacks, writers, process state, and a newly created PsychoPy window.

## Flip-locked transitions

Each current presentation and its authorized successor carry a unique
presentation ID and monotonic revision. The subject preloads assets, draws the
current state, schedules PTB audio against the next refresh where applicable,
and acknowledges the actual flip. It locally flips to the already-authorized
successor on the first refresh at or after the requested duration. Backend
deadlines detect missing acknowledgements but do not schedule normal display
duration.

At a direct boundary the backend writes the old phase end before the new phase
start using the same flip occurrence time. Stale IDs/revisions are rejected and
logged. If the successor is unavailable, or an interrupt supersedes it, the
subject flips neutral and reports the gap. Pause freezes remaining phase time;
resume requires another onset acknowledgement. Repeat and abort require a
neutral acknowledgement before state is superseded or finalized. When the
protocol is already paused, that acknowledged neutral frame is reused: repeat
prepares the new attempt while remaining paused, and abort can finalize without
requesting a redundant neutral flip. Another repeat issued before Resume
replaces the still-unpresented attempt without emitting phase/trial end events
or consuming an attempt number.

Subject/backend monotonic clocks are mapped from five ping/echo samples using
the lowest round-trip sample. Calibration is repeated before normal
finalization to expose drift. The session also retains the subject flip time,
UTC, backend receipt, and marker attempt timing.

## Failure policy

Malformed framing, protocol mismatch, duplicate roles, stale sessions, and
unknown message types are rejected. Subject loss or acknowledgement timeout
during execution fails the recording. Before execution, the subject may
reconnect and repeat preflight. Operator loss during recording starts a short
reconnect grace period before controlled failure. Display timing-quality misses
do not fail or abort a session. Preflight misses and active dropped frames are
shown to the operator as warnings and persisted in presentation metadata. Each
active dropped frame is attributed to its trial ID and attempt when available,
so affected trial attempts can be identified or excluded during analysis.
PsychoPy sends a timing-only IPC sample when a slow frame is detected, allowing
the operator warning to update without waiting for the next presentation
boundary and without advancing protocol state. Complete active intervals are
stored in `frame-intervals.csv`; preflight intervals are stored separately in
`preflight-frame-intervals.csv`.
