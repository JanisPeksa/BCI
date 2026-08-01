# psychoPy-SSVEP

A fully configurable, modular SSVEP experiment that renders stimuli with
**PsychoPy** while reusing the session recording, event writing, and clock
syncing machinery of `ssvep-bci`.

- Rendering: PsychoPy (`visual.Window` + persistent shapes, frame-locked
  flicker) — the pattern demonstrated in `phychopy-test/scripts/flicker_square.py`.
- Waveforms: **sinusoidal** (gamma-corrected) or **square** (rectangular
  meander, `scipy.signal.square`), selectable per stimulus.
- Multiple frequencies on the same screen are fully supported
  (`simultaneous_stimulus_ids`, dual-stimulus, multi-stimulus grid).
- Acquisition backends: deterministic **synthetic** EEG and **Cyton** via
  BrainFlow (OpenBCI). The `ssvep-bci` LSL backend is not included.
- Sessions are recorded as reconstructable folders (manifest, config snapshots,
  plan, `events.jsonl`, `eeg_raw.csv`, acquisition metadata/markers/health,
  checksums).

## Install in a virtual environment

From the repository's `BCI` directory:

```bash
python3.12 -m venv psychopy-ssvep/.venv
source psychopy-ssvep/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "./psychopy-ssvep[acquisition]"
```

`python3.11+` is required (`StrEnum`). The `acquisition` extra adds BrainFlow
and PySerial; omit it for synthetic-only use.

For BrainFlow/Cyton serial hardware on Linux, add your user to `dialout` and
log back in:

```bash
sudo usermod -aG dialout "$USER"
```

## Quick start

Validate the bundled configuration (no window, no session created):

```bash
psychopy-ssvep validate
psychopy-ssvep validate --config src/psychopy_ssvep/resources/configs/square.yaml
```

Run a windowed synthetic dry run (no hardware needed):

```bash
psychopy-ssvep run --participant TEST001 --windowed
```

Run a full-screen session with a custom config and session label:

```bash
psychopy-ssvep run --config "src/psychopy_ssvep/resources/configs/cyton.yaml" \
  --participant P012 --session-label three-frequency
```

Validate a recorded session folder:

```bash
psychopy-ssvep validate-session "sessions/20260723T120000Z_TEST001_abcd1234"
```

The `python -m psychopy_ssvep ...` form is equivalent to the `psychopy-ssvep`
console command.

## Configuration

Experiments are YAML files validated by strict Pydantic models. The two key
additions over `ssvep-bci` are the per-stimulus `waveform` and the PsychoPy
window block.

### Waveform option (per stimulus)

```yaml
stimuli:
  - id: target-1
    frequency_hz: 12.0
    phase_offset_radians: 0.0
    duty_cycle: 0.5
    waveform: sinusoidal   # or "square" (rectangular meander)
    visual:
      shape: rectangle     # or "circle"
      width_px: 200
      height_px: 200
      center_x: 0.5
      center_y: 0.5
      on_color: "#FFFFFF"
      off_color: "#000000"
```

- `sinusoidal` reproduces `generate_sinusoidal_wave` from the PsychoPy demo:
  `0.5 + 0.5·sin(2πf·t + φ)`, gamma-corrected via `presentation.psychopy.gamma`,
  mapped to PsychoPy RGB `[-1, 1]`. Brightness is interpolated between
  `off_color` and `on_color` per channel.
- `square` reproduces `signal.square`, binarizing to `-1/+1`; `duty_cycle` and
  `phase_offset_radians` apply.

### PsychoPy presentation block

```yaml
presentation:
  window_mode: full_screen     # or "windowed"
  screen_index: 0
  window_size_px: [1000, 700]  # used in windowed mode
  background_color: "#000000"
  hide_cursor: true
  refresh_preflight_frames: 120
  require_timing_quality: true
  max_frequency_error_hz: 0.25
  max_dropped_frame_fraction: 0.02
  psychopy:
    monitor_name: SSVEP_144Hz
    gamma: 2.169               # display gamma for the sinusoidal generator
    refresh_rate_hz: 144.0     # drives wave precompute + preflight target
    use_retina: false
    allow_gui: false
    check_timing: false
    units: pix
    monitor_size_px: [1920, 1080]
    interpolate: false
    message_color: "#808080"
    message_height_px: 40
    cue_border_color: "#FF3040"
    cue_border_width_px: 8
```

`refresh_rate_hz` should match the actual monitor refresh rate. Set
`require_timing_quality: true` to fail the session if the measured rate deviates
by more than `max_frequency_error_hz` or drops more than
`max_dropped_frame_fraction` frames. Stimulus frequencies must stay below half
the refresh rate (Nyquist).

### Multiple frequencies on one screen

The full `ssvep-bci` layout machinery is retained:

- `protocol.simultaneous_stimulus_ids`: several stimuli flash at once, each at
  its own frequency, while `stimulus_sequence` cycles the attended target.
- `dual_stimulus` / `multi_stimulus`: side-by-side or grid target/distractor
  layouts. By default these cycle the target through every position/side
  (`randomize_conditions: false` only disables *shuffling* — the target still
  sweeps positions in a fixed order). To keep every stimulus in one place for
  the whole session, enable `fixed_positions`:
  - `multi_stimulus.fixed_positions: true` + `target_position_id` (which grid
    position the target stays at; distractors fill the rest in their listed
    order), or
  - `dual_stimulus.fixed_positions: true` + `fixed_target_side`
    (`left`/`right`; the first distractor stays on the opposite side).

See `src/psychopy_ssvep/resources/configs/cyton.yaml` for a three-frequency
simultaneous-squares example.

### Device profiles

- `synthetic`: deterministic, marker-responsive EEG (no hardware).
- `cyton` (`backend: brainflow`): OpenBCI Cyton via BrainFlow, `board_id: 0`,
  auto-detected FTDI port.

Pick one with `device_profile` in the experiment config.

## Session artifacts

Each run creates a unique folder below `output.root_dir`:

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
checksums.sha256
```

`eeg_raw.csv` begins with sample index, receipt monotonic time, receipt UTC
time, source time, corrected source time, aligned monotonic time, named EEG
channels, and the embedded marker. Stimulus onset/offset events are timestamped
on the `time.perf_counter()` clock domain only after the corresponding
PsychoPy frame is swapped. Terminal manifests use `complete`, `aborted`,
`failed`, or `incomplete` semantics.

## Implementation notes

- `presentation/waves.py` — frame-indexed waveform generators.
- `presentation/renderer.py` — `PsychopyRenderer`; persistent shapes, placement
  reuse from `stimuli/models.py`, cue borders, message text.
- `presentation/loop.py` — preflight timing check, coordinator wiring, the
  frame-flip loop that acknowledges onset/offset frame swaps, and session
  finalization.
- The protocol, coordinator, acquisition, and recording modules are adapted
  from `ssvep-bci` with DSP/FBCCA, the Qt UI, and the LSL backend removed.
