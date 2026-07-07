"""
Standalone connection / data-flow diagnostic for the EEG board (BrainFlow).

Verifies, in order:
  1. The serial port can be found (auto-detects the FTDI/OpenBCI dongle).
  2. A BrainFlow session can be prepared and a stream started.
  3. Samples actually arrive, and at roughly the expected sampling rate.
  4. The EEG channels carry changing (non-flat) signal.

Usage:
    # Real Cyton dongle (auto-detect serial port)
    python check_connection.py --board-id=0

    # Real Cyton dongle, explicit port
    python check_connection.py --board-id=0 --serial-port=/dev/ttyUSB0

    # No hardware: synthetic board sanity check
    python check_connection.py --board-id=-1

Board IDs: Cyton=0, Cyton+Daisy=2, gTec Unicorn=8, Synthetic=-1
"""
import argparse
import sys
import time

import numpy as np

try:
    import serial.tools.list_ports as list_ports
except Exception:  # pyserial may be missing in odd setups
    list_ports = None

from brainflow.board_shim import BoardShim, BrainFlowInputParams


def find_serial_port():
    """Return the device path of the first FTDI (OpenBCI dongle) port, or None."""
    if list_ports is None:
        return None
    for port in list_ports.comports():
        manufacturer = (port.manufacturer or "")
        if "FTDI" in manufacturer:
            return port.device
    return None


def list_all_ports():
    if list_ports is None:
        return []
    return [(p.device, p.manufacturer) for p in list_ports.comports()]


def parse_args():
    parser = argparse.ArgumentParser(description="EEG board connection / data-flow check")
    parser.add_argument("--board-id", type=int, default=0,
                        help="BrainFlow board id (Cyton=0, Daisy=2, Unicorn=8, Synthetic=-1)")
    parser.add_argument("--serial-port", type=str, default="",
                        help="Serial port (auto-detected from FTDI if omitted)")
    parser.add_argument("--seconds", type=float, default=5.0,
                        help="How long to collect data for (seconds)")
    parser.add_argument("--flat-threshold", type=float, default=1e-6,
                        help="A channel with std below this (after demeaning) is treated as flat")
    return parser.parse_args()


def resolve_port(args):
    """Decide which serial port to use; synthetic board needs none."""
    if args.board_id == -1:
        return ""  # synthetic board ignores the serial port
    if args.serial_port:
        return args.serial_port
    detected = find_serial_port()
    if detected:
        print(f"[port] auto-detected FTDI dongle at: {detected}")
        return detected
    print("[port] WARNING: no FTDI dongle auto-detected.")
    ports = list_all_ports()
    if ports:
        print("[port] available serial ports:")
        for device, manufacturer in ports:
            print(f"         {device}  (manufacturer: {manufacturer})")
    else:
        print("[port] no serial ports found at all - is the dongle plugged in?")
    print("[port] re-run with an explicit --serial-port=/dev/ttyUSBx")
    return None


def main():
    args = parse_args()

    print("=" * 60)
    print(" EEG CONNECTION / DATA-FLOW CHECK")
    print("=" * 60)
    print(f"[cfg]  board-id : {args.board_id}")
    print(f"[cfg]  collect  : {args.seconds:.1f} s")

    serial_port = resolve_port(args)
    if serial_port is None:
        sys.exit(2)

    BoardShim.enable_dev_board_logger()

    params = BrainFlowInputParams()
    params.serial_port = serial_port

    # Describe the board (works without a connection).
    try:
        descr = BoardShim.get_board_descr(args.board_id)
        sampling_rate = int(descr["sampling_rate"])
        eeg_channels = descr.get("eeg_channels", [])
        marker_channel = descr.get("marker_channel", None)
        timestamp_channel = descr.get("timestamp_channel", None)
        print(f"[descr] sampling_rate   : {sampling_rate} Hz")
        print(f"[descr] eeg_channels    : {eeg_channels}")
        print(f"[descr] marker_channel  : {marker_channel}")
        print(f"[descr] timestamp_chan  : {timestamp_channel}")
    except Exception as error:
        print(f"[descr] FAILED to read board description: {error}")
        sys.exit(2)

    board = BoardShim(args.board_id, params)

    # 1) Prepare session (opens the serial/USB link).
    try:
        print("\n[step 1] preparing session ...")
        board.prepare_session()
        print("[step 1] OK - session prepared (link to board opened)")
    except Exception as error:
        print(f"[step 1] FAILED to prepare session: {error}")
        print("         likely causes: wrong serial port, dongle unplugged,")
        print("         missing permissions (add user to 'dialout'), or board off.")
        sys.exit(2)

    # 2) Start stream + collect.
    try:
        print("\n[step 2] starting stream ...")
        board.start_stream(45000)
        print(f"[step 2] OK - streaming; collecting for {args.seconds:.1f} s ...")
        time.sleep(args.seconds)
        data = board.get_board_data()  # shape: (num_rows, num_samples)
    except Exception as error:
        print(f"[step 2] FAILED during streaming: {error}")
        _safe_release(board)
        sys.exit(2)
    finally:
        pass

    # 3) Evaluate what we got.
    print("\n[step 3] evaluating collected data ...")
    num_samples = data.shape[1] if data.ndim == 2 else 0
    if num_samples == 0:
        print("[step 3] FAILED - zero samples received.")
        print("         the link opened but the board produced no data.")
        _safe_release(board)
        sys.exit(1)

    expected = sampling_rate * args.seconds
    effective_rate = num_samples / args.seconds
    print(f"[step 3] samples received : {num_samples}")
    print(f"[step 3] expected (approx): {int(expected)}")
    print(f"[step 3] effective rate   : {effective_rate:.1f} Hz "
          f"({100.0 * num_samples / max(expected, 1):.0f}% of expected)")

    # 4) Per-channel signal check on EEG channels.
    print("\n[step 4] per-channel EEG signal check ...")
    flat_channels = []
    for ch in eeg_channels:
        if ch >= data.shape[0]:
            continue
        channel_data = data[ch]
        std = float(np.std(channel_data))
        mean = float(np.mean(channel_data))
        flat = std < args.flat_threshold
        flag = "  <-- FLAT / no signal" if flat else ""
        if flat:
            flat_channels.append(ch)
        print(f"         ch {ch:>2}: mean={mean:12.3f}  std={std:10.3f}{flag}")

    # Verdict.
    print("\n" + "=" * 60)
    healthy_rate = num_samples >= 0.5 * expected
    has_signal = len(flat_channels) < len(eeg_channels)

    if healthy_rate and has_signal:
        print(" RESULT: PASS - board connected and streaming live data.")
        verdict = 0
    elif healthy_rate and not has_signal:
        print(" RESULT: PARTIAL - data flowing but all EEG channels are flat.")
        print("         check electrode contact / board power / channel gains.")
        verdict = 1
    else:
        print(" RESULT: PROBLEM - far fewer samples than expected.")
        print("         streaming is unstable or stalled.")
        verdict = 1
    print("=" * 60)

    _safe_release(board)
    sys.exit(verdict)


def _safe_release(board):
    """Stop stream and release session, ignoring errors during cleanup."""
    try:
        board.stop_stream()
    except Exception:
        pass
    try:
        board.release_session()
    except Exception:
        pass


if __name__ == "__main__":
    main()
