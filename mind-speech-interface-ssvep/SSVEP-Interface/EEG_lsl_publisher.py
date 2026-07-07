import pickle
import socket
from time import time
import numpy as np
from pylsl import StreamInlet, local_clock, resolve_byprop
from socket_utils import socket_send
class EEGLSLSocketPublisher:
    """Acquire EEG from an LSL stream and publish packets over TCP.
    Packet format matches EEGSocketPublisher: (N, num_channels + 1) with
    EEG channels in columns 0..num_channels-1 and a unix timestamp in the last column.
    """
    socket = None
    host = ''
    port = None
    inlet = None
    count = 0
    num_channels = None
    input_len = None
    def __init__(self, args):
        self.host = args.host
        self.port = args.lisPort
        self.input_len = args.input_len
        self.num_channels = args.num_channels
        self.lsl_name = args.lsl_name
        self.lsl_type = args.lsl_type
        self.lsl_timeout = args.lsl_timeout
        self.lsl_channel_indices = self._parse_channel_indices(args.lsl_channels)
        self._buffer = np.empty((0, self.num_channels + 1))
        self._time_offset = None
    @staticmethod
    def _parse_channel_indices(channel_spec):
        if not channel_spec:
            return None
        return [int(index) for index in channel_spec.split(',')]
    def open_socket_conn(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind((self.host, self.port))
        self.socket.listen()
    def close_socket_conn(self):
        self.socket.close()
    def _resolve_stream(self):
        print(f"Resolving LSL stream (type={self.lsl_type!r}, timeout={self.lsl_timeout}s)...")
        streams = resolve_byprop('type', self.lsl_type, timeout=self.lsl_timeout)
        if self.lsl_name:
            streams = [stream for stream in streams if stream.name() == self.lsl_name]
        if not streams:
            name_hint = f" with name {self.lsl_name!r}" if self.lsl_name else ""
            raise RuntimeError(
                f"No LSL stream found{type_hint} and type {self.lsl_type!r}. "
                "Start your EEG source with LSL output before running the streamer."
            )
        stream_info = streams[0]
        print(
            f"Connected to LSL stream: name={stream_info.name()!r}, "
            f"type={stream_info.type()!r}, channels={stream_info.channel_count()}, "
            f"srate={stream_info.nominal_srate()} Hz"
        )
        return StreamInlet(stream_info)
    def _configure_channels(self, stream_channel_count):
        if self.lsl_channel_indices is not None:
            if len(self.lsl_channel_indices) != self.num_channels:
                raise ValueError(
                    f"--lsl-channels must specify {self.num_channels} indices, "
                    f"got {len(self.lsl_channel_indices)}"
                )
            if max(self.lsl_channel_indices) >= stream_channel_count:
                raise ValueError(
                    f"LSL stream has {stream_channel_count} channels but "
                    f"--lsl-channels requests index {max(self.lsl_channel_indices)}"
                )
            self._channel_indices = self.lsl_channel_indices
            return
        if stream_channel_count < self.num_channels:
            raise ValueError(
                f"LSL stream has {stream_channel_count} channels but "
                f"{self.num_channels} are required"
            )
        self._channel_indices = list(range(self.num_channels))
    def open_board_conn(self, board_id=None, board_params=None, streamer_params=None):
        self.inlet = self._resolve_stream()
        self._configure_channels(self.inlet.info().channel_count())
        self.inlet.open_stream()
    def close_board_conn(self):
        if self.inlet is not None:
            self.inlet.close_stream()
            self.inlet = None
    def open_connections(self, board_id=None, board_params=None, streamer_params=None):
        self.open_socket_conn()
        self.open_board_conn(board_id, board_params, streamer_params)
    def close_connections(self):
        self.close_board_conn()
        self.close_socket_conn()
    def _append_chunk(self, samples, timestamps):
        if not timestamps:
            return
        if self._time_offset is None:
            self._time_offset = time() - local_clock()
        samples = np.asarray(samples, dtype=np.float64)
        if samples.ndim == 1:
            samples = samples.reshape(1, -1)
        eeg = samples[:, self._channel_indices]
        unix_timestamps = np.asarray(timestamps, dtype=np.float64) + self._time_offset
        rows = np.column_stack([eeg, unix_timestamps])
        if self._buffer.size:
            self._buffer = np.vstack([self._buffer, rows])
        else:
            self._buffer = rows
    def retrieve_sample(self):
        return self._buffer
    def send_packet(self, sample):
        socket_send(sending_socket=self.connection, data=sample)
        self.count += 1
    def publish(self, run_time=None):
        self.connection, self.address = self.socket.accept()
        with self.connection:
            print(f'Connected by {self.address}')
            print(f"Connected by: EEG_LSL_Socket: {time() * 1000:.0f} ms")
            init_time = time()
            time_func = (
                lambda: time() - init_time < run_time + 1
            ) if run_time else (lambda: True)
            while time_func():
                samples, timestamps = self.inlet.pull_chunk(timeout=0.1, max_samples=4096)
                self._append_chunk(samples, timestamps)
                if self._buffer.shape[0] >= self.input_len:
                    packet = self.retrieve_sample()
                    self.send_packet(packet)
                    self._buffer = np.empty((0, self.num_channels + 1))
                    print("packet sent")
            self.connection.sendall(pickle.dumps(None))
