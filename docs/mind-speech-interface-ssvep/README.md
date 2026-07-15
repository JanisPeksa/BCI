# Mind Speech Interface — SSVEP

## Overview

`mind-speech-interface-ssvep` is an EEG-based brain–computer interface built around Steady-State Visual Evoked Potentials (SSVEP). A user looks at flickering visual controls, and the resulting EEG response frequency is classified as a command. Commands can navigate menus, type text, answer yes/no questions, generate speech, or select external output actions.

This is an SSVEP selection interface rather than unrestricted speech-from-thought recognition. The user selects a finite set of commands by visually attending to stimuli with known frequencies.

## Functionality

### Offline data collection and analysis

- Collects EEG data using OpenBCI Cyton, g.tec Unicorn Hybrid, or a BrainFlow synthetic board.
- Presents 4, 6, or 8 configurable flickering stimuli.
- Uses default stimulus frequencies from 8.25 to 14.25 Hz.
- Saves recordings as CSV files.
- Provides Jupyter-based EEG visualization and FFT plots by electrode and stimulus.
- Supports training and evaluation of SSVEP models.

### Online interface

The real-time interface provides:

- Output menu with Twitter, voice, and help options.
- Keyboard/yes-no selection menu.
- Yes/no page with audio feedback.
- Hierarchical SSVEP keyboard for character and word entry.
- Space, backspace, and word/character toggle controls.
- Page-specific stimulus-frequency mappings.
- Online EEG recording to CSV.

### Optional integrations

- OpenAI completion-based word and sentence prediction.
- Google Cloud Speech-to-Text for spoken prompt input.
- gTTS and `playsound` for text-to-speech output.
- Twitter integration through Tweepy.
- React web application that sends prompt text to the desktop GUI using WebSockets.

## Architecture

```text
EEG headset or synthetic board
            |
            v
BrainFlow or LSL acquisition
            |
            v
EEG socket publisher
            |
            v
DSP_Client
  - buffers EEG samples
  - aligns samples with stimulus timing
  - filters EEG
  - runs the selected model
            |
            v
Predicted SSVEP frequency
            |
            v
PyQt5 interface
  - maps frequency to a button
  - navigates pages
  - edits text
  - triggers output actions
```

The online launcher, `SSVEP-Interface/Data_Streamer.py`, starts three processes:

1. EEG streaming from BrainFlow or Lab Streaming Layer.
2. DSP and model inference in `DSP_Client.py`.
3. The PyQt5 GUI in `SSVEP-Interface/main.py`.

The GUI sends stimulus onset/offset and current-page information to the DSP process over a TCP socket. The DSP process uses this timing information to extract the EEG window associated with each stimulus. Model predictions are sent back to the UI over another socket.

The desktop GUI also runs a WebSocket server, normally on port `8765`, for the React prompt-input application. The UI timing socket uses port `32111`; the main EEG and prediction sockets use configurable localhost ports.

## SSVEP signal-processing pipeline

The standard online flow is:

1. Receive EEG samples at approximately 250 Hz.
2. Buffer data until a stimulus interval is complete.
3. Extract a fixed-length window, normally about 4.5 seconds.
4. Remove the timestamp column.
5. Apply notch or band-pass filtering.
6. Compare the signal with sinusoidal reference templates and harmonics.
7. Select the frequency with the highest score.
8. Convert that frequency into a page-specific UI command.

## Models

### FBCCA

`eeg_ai_layer/models/FBCCA.py` implements Filter-Bank Canonical Correlation Analysis:

- Creates sine/cosine reference templates.
- Includes configurable harmonics.
- Applies a 60 Hz notch filter.
- Uses multiple filter-bank bands.
- Calculates CCA correlations for each candidate frequency.
- Combines filter-bank scores with weighted coefficients.

### CCA-KNN

`eeg_ai_layer/models/CCAKNN.py` uses CCA correlation values as features for a KNN classifier. Models can be saved and loaded with `joblib`.

### Model selection

`eeg_ai_layer/models/Model.py` selects the requested implementation:

- `cca_knn`
- `fbcca`
- `fbcca_knn`

## Main repository areas

| Area | Location |
|---|---|
| Project documentation | `mind-speech-interface-ssvep/README.md` |
| Online launcher | `SSVEP-Interface/Data_Streamer.py` |
| Desktop GUI | `SSVEP-Interface/main.py`, `SSVEP-Interface/UI/` |
| EEG/DSP client | `SSVEP-Interface/DSP_Client.py` |
| EEG publishers | `EEG_socket_publisher.py`, `EEG_lsl_publisher.py` |
| Models | `eeg_ai_layer/models/` |
| Offline collection | `SSVEP-Data-Collection/` |
| Visualization | `EEG-Data-Visualization/` |
| Web prompt input | `SSVEP-Interface/web-app/` |
| Speech recognition | `SSVEP-Interface/SpeechRecognition/` |
| Text-to-speech | `SSVEP-Interface/_TTS/` and `UI/YNPage/YN.py` |
| Mechanical designs | `Mech-Design-Files/` |

## Libraries and tools

### Python

- BrainFlow — EEG hardware and synthetic-board access
- NumPy and pandas — numerical processing and recording
- SciPy — filtering and signal processing
- scikit-learn — CCA, KNN, metrics, and confusion matrices
- PyQt5 — desktop interface
- pylsl — Lab Streaming Layer input
- pyserial and OpenBCI Python — hardware support
- websockets and socket libraries — process and browser communication
- OpenAI — word and sentence prediction
- Google Cloud Speech — speech recognition
- Tweepy — Twitter API access
- gTTS and playsound — speech synthesis and playback
- Jupyter Notebook — offline analysis

### Frontend

The web application uses React 18, React DOM, Create React App, Material UI, Emotion, Testing Library, and Web Vitals.

## Setup and execution

The original project targets Windows 10 or later and requires Python 3.10+ according to its documentation. Install the base dependencies from the project directory:

```text
pip install -r requirements.txt
```

For offline collection, configure `SSVEP-Data-Collection/configs.py`, especially `NUM_STIMS`, then run the appropriate `run_demo.py` command for the selected EEG board.

For online execution, start `SSVEP-Interface/Data_Streamer.py` with either a real board, an LSL source, or the BrainFlow synthetic board. The model type and optional saved-model path are provided as command-line arguments.

## Limitations and risks

- The project is a research/demo prototype, not a production-ready package.
- Some imports are not represented in `requirements.txt`, including Flask, Flask-SocketIO, gTTS, sounddevice, pydub, and possibly joblib.
- Several integrations use legacy APIs, particularly the OpenAI completion interface.
- Speech recognition contains a hard-coded FFmpeg path and a checked-in service-account credential file; credentials should be removed from source control and configured through secure environment variables.
- Socket ports and some paths are hard-coded or assumed to be local.
- Qt widgets are accessed from worker threads, which can cause thread-safety problems.
- Accurate stimulus timing is essential because the classifier extracts EEG windows from GUI onset/offset timestamps.
- Naming and path inconsistencies exist, including `eeg-ai-layer` versus `eeg_ai_layer`.
- The achievable vocabulary is constrained by the number of visual stimulus frequencies and the UI hierarchy.

## Summary

The system combines EEG acquisition, SSVEP signal processing, classical machine-learning classification, and a command-oriented GUI. Its central design is a three-process pipeline: an EEG streamer publishes samples, a DSP client classifies stimulus responses, and a PyQt5 interface turns predicted frequencies into navigation and text commands. Offline tools support data collection, visualization, and model training, while optional cloud and web integrations extend the interface with speech, word prediction, and external output.
