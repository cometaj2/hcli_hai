import threading
import traceback
import numpy as np
import sounddevice as sd
import logger
from piper.voice import PiperVoice

log = logger.Logger()


class TerminationException(Exception):
    pass


class Voice:
    """Threaded Piper playback. stop() aborts only the current utterance."""

    def __init__(self, model_path, check_termination=None):
        self.model_path = model_path
        self.check_termination = check_termination or (lambda: None)
        self.voice = None
        self.sample_rate = None
        self._lock = threading.RLock()
        self._thread = None
        self._stream = None
        self._cancel = None  # Event for the in-flight utterance only

    def speak(self, message):
        if not message:
            return
        log.info("[ hai ] " + message)

        cancel = threading.Event()
        self.stop()
        t = threading.Thread(
            target=self._worker,
            args=(message, cancel),
            name="hai-voice",
            daemon=True,
        )
        with self._lock:
            self._cancel = cancel
            self._thread = t
        t.start()

    def stop(self):
        with self._lock:
            cancel = self._cancel
            t = self._thread
            stream = self._stream
        if cancel is not None:
            cancel.set()
        self._close_stream(stream)
        if t is not None and t is not threading.current_thread() and t.is_alive():
            t.join(timeout=1.0)

    def is_speaking(self):
        with self._lock:
            t = self._thread
        return t is not None and t.is_alive()

    def wait(self, timeout=None):
        with self._lock:
            t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=timeout)

    def _ensure_voice(self):
        if self.voice is None and self.model_path:
            self.voice = PiperVoice.load(self.model_path)
            self.sample_rate = self.voice.config.sample_rate

    def _worker(self, message, cancel):
        stream = None
        try:
            if cancel.is_set():
                return
            self._ensure_voice()
            if self.voice is None or cancel.is_set():
                return

            stream = sd.RawOutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
            )
            with self._lock:
                if cancel.is_set():
                    stream.close()
                    return
                self._stream = stream
            stream.start()

            for chunk in self.voice.synthesize(message):
                if cancel.is_set():
                    raise TerminationException("[ hai ] voice stopped")
                self.check_termination()
                audio_chunk = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                stream.write(audio_chunk)
        except TerminationException:
            log.warning("[ hai ] speaking terminated")
        except sd.PortAudioError:
            log.warning("[ hai ] port audio error. speaking terminated")
        except Exception:
            log.error(traceback.format_exc())
        finally:
            self._close_stream(stream)
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                    self._cancel = None
                    if self._stream is stream:
                        self._stream = None

    def _close_stream(self, stream=None):
        if stream is None:
            with self._lock:
                stream = self._stream
                self._stream = None
        if stream is None:
            return
        try:
            stream.abort()
        except Exception:
            pass
