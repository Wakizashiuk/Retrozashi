"""Push-to-talk speech-to-text: toggle recording with a hotkey, transcribe locally with faster-whisper.

Audio never leaves the machine. The model loads lazily on first use (a few seconds), then
`small.en` transcribes a 5 s command in roughly 0.3 s on a modern GPU, or ~1.5 s on CPU.
"""
from __future__ import annotations

import threading
from typing import Callable

from ..config import VoiceSettings

SAMPLE_RATE = 16_000
MAX_SECONDS = 20


class VoiceInput:
    def __init__(self, settings: VoiceSettings, on_text: Callable[[str], None],
                 on_state: Callable[[str], None] = lambda s: None) -> None:
        self.settings = settings
        self.on_text = on_text
        self.on_state = on_state
        self._model = None
        self._chunks: list = []
        self._stream = None
        self._lock = threading.Lock()

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def toggle(self) -> None:
        with self._lock:
            if self._stream is None:
                self._start()
            else:
                self._stop_and_transcribe()

    def stop(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stop_and_transcribe()

    def _start(self) -> None:
        import sounddevice as sd

        self._chunks = []

        def callback(indata, frames, time_info, status):
            self._chunks.append(indata.copy())
            if len(self._chunks) * frames > SAMPLE_RATE * MAX_SECONDS:
                threading.Thread(target=self.stop, daemon=True).start()  # cap length

        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback)
        self._stream.start()
        self.on_state("listening")

    def _stop_and_transcribe(self) -> None:
        import numpy as np

        stream, self._stream = self._stream, None
        stream.stop()
        stream.close()
        if not self._chunks:
            self.on_state("idle")
            return
        audio = np.concatenate(self._chunks)[:, 0]
        self.on_state("transcribing")
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            device = self.settings.device
            if device == "auto":
                try:
                    import ctranslate2
                    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    device = "cpu"
            compute = "float16" if device == "cuda" else "int8"
            self._model = WhisperModel(self.settings.whisper_model, device=device, compute_type=compute)
        return self._model

    def _transcribe(self, audio) -> None:
        try:
            segments, _ = self._load().transcribe(
                audio, language="en", beam_size=5, vad_filter=True,
                initial_prompt=self.settings.initial_prompt,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            if text:
                self.on_text(text)
        finally:
            self.on_state("idle")
