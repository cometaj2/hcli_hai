import sys
import threading
import traceback
import numpy as np
import sounddevice as sd
import logger

import os
import sys

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

            with AudioDucker(duck_to=0.25) as ducker:
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
                ducker.protect_new()

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


# Lower other apps' playback while TTS speaks.
# Linux (PipeWire/Pulse) only. Other platforms are a no-op so the
# same context-manager call site works everywhere.
class AudioDucker:

    def __init__(self, duck_to=0.25, exclude_pids=None):
        self.duck_to = max(0.0, min(1.0, float(duck_to)))
        self.exclude_pids = {
            int(p) for p in (exclude_pids if exclude_pids is not None else [os.getpid()])
        }
        self._saved = {}
        self._known = set()
        self._lock = threading.Lock()

    def __enter__(self):
        self._snapshot_and_duck()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._restore()
        return False

    def _snapshot_and_duck(self):
        if sys.platform.startswith("linux"):
            self._linux_duck()

    def _restore(self):
        if sys.platform.startswith("linux"):
            self._linux_restore()

    def _linux_pulse(self):
        try:
            import pulsectl
        except ImportError:
            log.warning("[ hai ] pulsectl not installed; audio ducking disabled")
            return None
        try:
            return pulsectl.Pulse("hai-ducker")
        except Exception as e:
            log.warning("[ hai ] pulse/pipewire unavailable; audio ducking disabled: %s", e)
            return None

    def protect_new(self):
        """Force full volume on sink-inputs that appeared after ducking."""
        if not sys.platform.startswith("linux"):
            return
        pulse = self._linux_pulse()
        if pulse is None:
            return
        try:
            with self._lock:
                known = set(self._known)
            for si in pulse.sink_input_list():
                if si.index in known:
                    continue
                try:
                    pulse.volume_set_all_chans(si, 1.0)
                    log.info("[ hai ] left TTS stream %s at full volume", si.index)
                except Exception as e:
                    log.debug("[ hai ] could not protect sink-input %s: %s", si.index, e)
        except Exception as e:
            log.warning("[ hai ] protect_new failed: %s", e)
        finally:
            try:
                pulse.close()
            except Exception:
                pass

    def _linux_duck(self):
        pulse = self._linux_pulse()
        if pulse is None:
            return
        try:
            inputs = list(pulse.sink_input_list())
            known = {si.index for si in inputs}
            saved = {}
            for si in inputs:
                if getattr(si, "mute", False):
                    continue
                try:
                    original = si.volume.value_flat
                except Exception:
                    continue
                if original <= self.duck_to:
                    continue
                try:
                    pulse.volume_set_all_chans(si, self.duck_to)
                except Exception as e:
                    log.debug("[ hai ] could not duck sink-input %s: %s", si.index, e)
                    continue
                saved[si.index] = original
            with self._lock:
                self._saved = saved
                self._known = known
            if saved:
                log.info("[ hai ] ducked %d audio stream(s) to %.0f%%",
                         len(saved), self.duck_to * 100)
        except Exception as e:
            log.warning("[ hai ] linux duck failed: %s", e)
        finally:
            try:
                pulse.close()
            except Exception:
                pass

    def _linux_restore(self):
        with self._lock:
            saved = self._saved
            self._saved = {}
            self._known = set()
        if not saved:
            return
        pulse = self._linux_pulse()
        if pulse is None:
            return
        try:
            by_idx = {si.index: si for si in pulse.sink_input_list()}
            restored = 0
            for idx, original in saved.items():
                si = by_idx.get(idx)
                if si is None:
                    continue
                try:
                    pulse.volume_set_all_chans(si, original)
                    restored += 1
                except Exception as e:
                    log.debug("[ hai ] could not restore sink-input %s: %s", idx, e)
            if restored:
                log.info("[ hai ] restored %d audio stream(s)", restored)
        except Exception as e:
            log.warning("[ hai ] linux unduck failed: %s", e)
        finally:
            try:
                pulse.close()
            except Exception:
                pass

