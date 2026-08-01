---
name: convert-ssvep-session-to-xdf
description: Convert recorded SSVEP session folders containing eeg_raw.csv into validated XDF files while preserving the source folder. Use for requests to export, convert, or package ssvep-bci recordings as XDF, especially when a session-specific YAML configuration or BCI analysis .pyp pipeline must be preserved in metadata.
---

# Convert SSVEP Session to XDF

Convert a completed `ssvep-bci` session into a sibling `.xdf` file. Treat the
session artifacts as read-only: never place the output inside the session
folder, never rewrite its manifest/checksums, and never overwrite an existing
XDF unless the user explicitly authorizes it.

## Workflow

1. Inspect `eeg_raw.csv`, `acquisition-metadata.json`, `manifest.json`,
   `events.jsonl`, `experiment-config.yaml`, and any user-specified reference
   YAML or `.pyp` pipeline.
2. Confirm the session exists and that the referenced configuration matches its
   `experiment_id`.
3. Run the bundled converter:

   ```powershell
   & .\\.venv\\Scripts\\python.exe `
     .\\.codex\\skills\\convert-ssvep-session-to-xdf\\scripts\\convert_session_to_xdf.py `
     <session-path> --config <config-path> --pyp <pipeline.pyp>
   ```

   Omit `--config` or `--pyp` when no external reference is supplied.
4. Default the output to a sibling named `<session-path>.xdf`. Refuse to
   overwrite it unless `--overwrite` is explicitly requested.
5. Report the absolute output path, stream/sample counts, protocol metadata,
   validation status, and SHA-256 hash.

## XDF stream contract

Create these exact stream names because project `.pyp` pipelines select by
name:

- `Cyton EEG`: timestamped rows from `eeg_raw.csv`, labels/count from
  `acquisition-metadata.json`, timestamps from `source_timestamp`, format
  `double64`.
- `BrainFlow Embedded Markers`: sparse non-zero `embedded_marker` values with
  their EEG timestamps; one `int32` channel named `marker`.
- `SSVEP Session Events`: every `events.jsonl` record as a compact UTF-8 JSON
  string; one `string` channel named `event`.

Convert event timestamps into the EEG Unix-epoch domain with:

```text
event_xdf_timestamp = event.monotonic_timestamp -
                       acquisition_metadata.backend_metadata.wall_to_monotonic_offset
```

Use the same Unix-epoch domain for all streams. Include config file,
experiment ID, timing, target/distractor stimuli, candidate frequencies,
stimulus layout/square count/positions, and `.pyp` identity in each stream
header's `<desc>` when available. Support both `dual_stimulus` and
`multi_stimulus` configuration sections; do not infer a fixed number of
frequencies or squares.

## Validation and safety

The converter must write XDF 1.0-compatible little-endian chunks; check
contiguous sample indices and monotonic timestamps; verify metadata counts;
parse the generated XDF, XML, footers, stream names, and counts; run
`ssvep_bci.recording.validate_session` when available; and confirm that no XDF
was created inside the source session directory. If validation fails, preserve
the source and report the failure instead of claiming completion.

## Bundled resource

Use [`scripts/convert_session_to_xdf.py`](scripts/convert_session_to_xdf.py)
for deterministic conversion and post-write validation. Read or patch it only
when a repository-specific format variation requires it.

