# Target-frequency FFT pipeline

`SSVEP-Research-six-frequency-target-fft.pyp` is the Neuropype-compatible
pipeline for the recorded six-frequency session. It applies the repository's
high-pass and artifact-cleaning chain, selects JSON `stimulus_onset` events by
the configured target identity (`freq-12-75`, 12.75 Hz), segments 0.25–1.75
seconds after onset, computes a one-sided FFT at the native epoch length,
averages all 24 target trials, and plots all eight EEG channels. Pooling all 24
trials is intentional: the target frequency is fixed while its screen position
is randomized across six positions with four repetitions per position.

`SSVEP-Research-six-frequency-target-fft-4s.pyp` applies the same processing
to the four-second session `20260801T133429Z_yehor_k_session_1275_4s_290e0084`.
Its steady-state epoch is 0.25–3.75 seconds after target onset.

The reproducible command-line renderer is `fft_target_frequency.py`:

```powershell
& .\.venv\Scripts\python.exe `
  .\psychopy-ssvep\analysis\fft_target_frequency.py `
  .\psychopy-ssvep\sessions\20260801T125552Z_yehor_k_session_1275_with_b_be5c07a2.xdf `
  --output-dir .\psychopy-ssvep\sessions\fft-analysis
```

The command-line renderer independently writes a PNG plot and a JSON report.
It uses a Hann window and zero-padding for a smooth display and records those
FFT settings, selected trials, target-bin magnitude, and strongest 5–20 Hz
peak in the report.
