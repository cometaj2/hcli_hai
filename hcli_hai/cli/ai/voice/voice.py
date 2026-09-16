import sys
import time
import threading
import traceback
import numpy as np
import sounddevice as sd
import logger
import os
import config as c
from piper.voice import PiperVoice

from ai.voice import audioducker as a

log = logger.Logger()


# Threaded Piper playback.
# stop() from any other thread only signals cancel and waits.
# The worker thread is the only one that start/write/stop/close the PortAudio stream.
class Voice:
    _instance = None
    _init_lock = threading.RLock()
    _WRITE_FRAMES = 1024

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.initialized = False
            return cls._instance

    def __init__(self):
        if self.initialized:
            return
        with self._init_lock:
            if self.initialized:
                return
            self.config = c.Config()
            self.model_path = self.config.assistant_tts_path
            self.voice = None
            self.sample_rate = None
            self._lock = threading.RLock()
            self._thread = None
            self._stream = None
            self._cancel = None
            self._done = threading.Event()
            self._done.set()
            self._gen = 0
            self.initialized = True
            self.terminate = False

    def speak(self, message):
        if not message:
            return
        log.info("[ hai ] " + message)

        self.stop()

        cancel = threading.Event()
        done = threading.Event()
        with self._lock:
            self.terminate = False
            self._gen += 1
            gen = self._gen
            self._cancel = cancel
            self._done = done
            self._stream = None
            t = threading.Thread(
                target=self._worker,
                args=(message, cancel, done, gen),
                name="hai-voice",
                daemon=True,
            )
            self._thread = t
        t.start()

    # Abort the in-flight utterance from any thread.
    # Signals the worker and waits for it to close its own stream.
    # Does not touch the PortAudio stream object.
    def stop(self, wait=True, timeout=2.0):
        with self._lock:
            self.terminate = True
            cancel = self._cancel
            done = self._done
            t = self._thread
        if cancel is not None:
            cancel.set()
        if not wait:
            return
        if t is None or t is threading.current_thread():
            a.AudioDucker.restore_foreign_to_full()
            return
        done.wait(timeout=timeout)
        t.join(timeout=0.2)
        if t.is_alive():
            log.debug(
                "Voice thread still running after timeout; "
                "leaving stream for the worker to close to avoid ALSA SIGSEGV"
            )
        a.AudioDucker.restore_foreign_to_full()

    def is_speaking(self):
        return not self._done.is_set()

    def wait(self, timeout=None):
        self._done.wait(timeout=timeout)

    def _ensure_voice(self):
        if self.voice is None and self.model_path:
            self.voice = PiperVoice.load(self.model_path)
            self.sample_rate = self.voice.config.sample_rate

    def _should_stop(self, cancel):
        if cancel is not None and cancel.is_set():
            return True
        if self.terminate:
            if cancel is not None:
                cancel.set()
            return True
        return False

    def _write_chunk(self, stream, audio_chunk, cancel):
        if audio_chunk.size == 0:
            return True
        if not audio_chunk.flags.c_contiguous:
            audio_chunk = np.ascontiguousarray(audio_chunk)
        step = self._WRITE_FRAMES
        for i in range(0, len(audio_chunk), step):
            if self._should_stop(cancel):
                return False
            stream.write(audio_chunk[i:i + step])
        return True

    def _worker(self, message, cancel, done, gen):
        stream = None
        try:
            if self._should_stop(cancel):
                return
            self._ensure_voice()
            if self.voice is None or self._should_stop(cancel):
                return

            with a.AudioDucker(duck_to=0.25, fade_s=0.8, fade_steps=20) as ducker:
                if self._should_stop(cancel):
                    return

                stream = sd.RawOutputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=self._WRITE_FRAMES,
                )
                with self._lock:
                    if self._should_stop(cancel) or self._gen != gen:
                        stream.close()
                        stream = None
                        return
                    self._stream = stream

                stream.start()
                ducker.protect_new()

                for chunk in self.voice.synthesize(message):
                    if self._should_stop(cancel):
                        log.debug("[ hai ] speaking terminated")
                        break
                    audio_chunk = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                    if not self._write_chunk(stream, audio_chunk, cancel):
                        log.debug("[ hai ] speaking terminated")
                        break
        except TerminationException:
            log.debug("[ hai ] speaking terminated")
        except sd.PortAudioError:
            log.debug("[ hai ] port audio error. speaking terminated")
        except Exception:
            log.error(traceback.format_exc())
        finally:
            self._release_stream(stream, gen)
            done.set()
            a.AudioDucker.restore_foreign_to_full()

    def _release_stream(self, stream, gen):
        if stream is not None:
            try:
                if getattr(stream, "active", False):
                    stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        with self._lock:
            if self._gen != gen:
                return
            if self._thread is threading.current_thread():
                self._thread = None
            if self._cancel is not None and self._cancel.is_set():
                self._cancel = None
            if self._stream is stream:
                self._stream = None

class TerminationException(Exception):
    pass
