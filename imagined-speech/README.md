# Imagined Speech

Configurable research application for FEIS-style imagined-speech EEG
experiments. Milestone 2 provides strict configuration, deterministic protocol
planning, simulated execution, a subject-facing Qt display, and reconstructable
session packages. EEG acquisition follows in milestone 3.

## Setup

From this directory, create or activate a Python 3.11 environment and install
the current milestone:

```powershell
python -m pip install -e ".[ui,dev]"
```

Future UI and acquisition milestones can be installed with:

```powershell
python -m pip install -e ".[ui,acquisition,dev]"
```

## Commands

With no configuration argument, commands use the bundled hardware-free smoke
profile.

```powershell
python main.py validate
python main.py preview
python main.py preview --config imagined_speech/resources/configs/imagined_only.yaml
python main.py preview --config imagined_speech/resources/configs/feis_comparable.yaml
python main.py simulate
python main.py run --windowed
python main.py validate-session sessions/<session-directory>
```

Running `python main.py` without a command prints command help. Configuration
and device paths are resolved relative to the YAML file that contains them.

`simulate` uses the fast virtual clock by default and writes a complete session
package. Use `--clock real` for wall-clock execution. `run` uses the real clock
and configured subject screen by default; `--clock virtual --windowed` provides
a fast visual development run. Press Escape to abort a subject run safely.
