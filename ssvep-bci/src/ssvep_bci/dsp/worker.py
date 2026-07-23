from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from ssvep_bci.config.loader import ResolvedExperiment
from ssvep_bci.dsp.contracts import ProcessingResult
from ssvep_bci.dsp.processors import FbccaProcessor
from ssvep_bci.dsp.windows import SampleRingBuffer, WindowRequest, make_window


_STOP = object()


class DspService:
    def __init__(
        self,
        resolved: ResolvedExperiment,
        ring: SampleRingBuffer,
        on_result: Callable[[ProcessingResult], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self.resolved = resolved
        self.ring = ring
        self.on_result = on_result
        self.on_error = on_error
        self.processor = FbccaProcessor(resolved)
        self._queue: queue.Queue[WindowRequest | object] = queue.Queue(maxsize=16)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.resolved.config.processing.enabled:
            return
        self._thread = threading.Thread(target=self._run, name="ssvep-dsp", daemon=True)
        self._thread.start()

    def submit(self, request: WindowRequest) -> None:
        if not self.resolved.config.processing.enabled:
            return
        try:
            self._queue.put_nowait(request)
        except queue.Full:
            self.on_error(RuntimeError("DSP queue overrun"))

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        deadline = time.monotonic() + 30.0
        while thread.is_alive():
            try:
                self._queue.put(_STOP, timeout=0.1)
                break
            except queue.Full:
                if time.monotonic() >= deadline:
                    raise RuntimeError("DSP queue did not drain during shutdown")
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            raise RuntimeError("DSP worker did not stop")
        self._thread = None

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, WindowRequest)
                window = make_window(item, self.resolved, self.ring)
                self.on_result(self.processor.process(window))
            except Exception as exc:
                self.on_error(exc)
            finally:
                self._queue.task_done()
