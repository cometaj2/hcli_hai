import sys
import time
import threading
import traceback
import numpy as np
import sounddevice as sd
import logger
import os
import config as c
from scipy.signal import butter, sosfilt, sosfilt_zi, tf2sos
from piper.voice import PiperVoice, SynthesisConfig
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
            self.enhance = VoiceEnhancer(self.sample_rate)

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
            self.enhance = VoiceEnhancer(self.sample_rate)
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

                syn = SynthesisConfig(
                    length_scale=1.08,
                    noise_scale=0.28,
                    noise_w_scale=0.35,
                    normalize_audio=True,
                )

                for chunk in self.voice.synthesize(message, syn_config=syn):
                    if self._should_stop(cancel):
                        log.debug("[ hai ] speaking terminated")
                        break
                    audio_chunk = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                    audio_chunk = self.enhance.process(audio_chunk)
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

class VoiceSmoother:
    def __init__(self, sr):
        self.sos_hp = butter(2, 90 / (sr / 2), btype="highpass", output="sos")
        self.sos_lp = butter(3, 8500 / (sr / 2), btype="lowpass", output="sos")
        self.zi_hp = sosfilt_zi(self.sos_hp)
        self.zi_lp = sosfilt_zi(self.sos_lp)

    def process(self, x_i16: np.ndarray) -> np.ndarray:
        x = x_i16.astype(np.float32) / 32768.0
        x, self.zi_hp = sosfilt(self.sos_hp, x, zi=self.zi_hp)
        x, self.zi_lp = sosfilt(self.sos_lp, x, zi=self.zi_lp)
        # gentle high-shelf cut ~ -4 dB above 7 kHz (one-pole-ish)
        # skip if you already LPF aggressively
        peak = np.max(np.abs(x)) + 1e-8
        if peak > 0.98:
            x *= 0.98 / peak
        return np.clip(x * 32767.0, -32768, 32767).astype(np.int16)

def _rbj_peaking_sos(sr, f0, q, gain_db):
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / sr
    cosw, sinw = np.cos(w0), np.sin(w0)
    alpha = sinw / (2.0 * q)
    b0 = 1.0 + alpha * a
    b1 = -2.0 * cosw
    b2 = 1.0 - alpha * a
    a0 = 1.0 + alpha / a
    a1 = -2.0 * cosw
    a2 = 1.0 - alpha / a
    return tf2sos([b0 / a0, b1 / a0, b2 / a0], [1.0, a1 / a0, a2 / a0])


def _rbj_lowshelf_sos(sr, f0, q, gain_db):
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / sr
    cosw, sinw = np.cos(w0), np.sin(w0)
    alpha = sinw / (2.0 * q)
    two_sa = 2.0 * np.sqrt(a) * alpha
    b0 = a * ((a + 1.0) - (a - 1.0) * cosw + two_sa)
    b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cosw)
    b2 = a * ((a + 1.0) - (a - 1.0) * cosw - two_sa)
    a0 = (a + 1.0) + (a - 1.0) * cosw + two_sa
    a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cosw)
    a2 = (a + 1.0) + (a - 1.0) * cosw - two_sa
    return tf2sos([b0 / a0, b1 / a0, b2 / a0], [1.0, a1 / a0, a2 / a0])


def _rbj_highshelf_sos(sr, f0, q, gain_db):
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / sr
    cosw, sinw = np.cos(w0), np.sin(w0)
    alpha = sinw / (2.0 * q)
    two_sa = 2.0 * np.sqrt(a) * alpha
    b0 = a * ((a + 1.0) + (a - 1.0) * cosw + two_sa)
    b1 = -2.0 * a * ((a - 1.0) + (a + 1.0) * cosw)
    b2 = a * ((a + 1.0) + (a - 1.0) * cosw - two_sa)
    a0 = (a + 1.0) - (a - 1.0) * cosw + two_sa
    a1 = 2.0 * ((a - 1.0) - (a + 1.0) * cosw)
    a2 = (a + 1.0) - (a - 1.0) * cosw - two_sa
    return tf2sos([b0 / a0, b1 / a0, b2 / a0], [1.0, a1 / a0, a2 / a0])


class VoiceEnhancer:
    def __init__(self, sr):
        sr = float(sr)
        z = lambda sos: np.zeros_like(sosfilt_zi(sos))

        self.sos_hp = butter(2, 70.0 / (sr / 2.0), btype="highpass", output="sos")
        self.zi_hp = z(self.sos_hp)

        self.sos_body = _rbj_lowshelf_sos(sr, f0=170.0, q=0.70, gain_db=+2.0)
        self.zi_body = z(self.sos_body)
        self.sos_box = _rbj_peaking_sos(sr, f0=480.0, q=1.10, gain_db=-1.2)
        self.zi_box = z(self.sos_box)
        self.sos_pres = _rbj_peaking_sos(sr, f0=2600.0, q=0.90, gain_db=+1.8)
        self.zi_pres = z(self.sos_pres)
        self.sos_air = _rbj_highshelf_sos(sr, f0=6500.0, q=0.70, gain_db=-2.5)
        self.zi_air = z(self.sos_air)

        self.rms = 1e-6
        self.dbx_thresh_db = -12.0
        self.dbx_ratio = 1.12
        self.dbx_max_boost_db = 2.5
        self.dbx_atk = 1.0 - np.exp(-1.0 / (0.008 * sr))
        self.dbx_rel = 1.0 - np.exp(-1.0 / (0.120 * sr))

        self._fade_left = int(0.008 * sr)

    def process(self, x_i16: np.ndarray) -> np.ndarray:
        x = x_i16.astype(np.float32) / 32768.0
        x, self.zi_hp = sosfilt(self.sos_hp, x, zi=self.zi_hp)
        x, self.zi_body = sosfilt(self.sos_body, x, zi=self.zi_body)
        x, self.zi_box = sosfilt(self.sos_box, x, zi=self.zi_box)
        x, self.zi_pres = sosfilt(self.sos_pres, x, zi=self.zi_pres)
        x, self.zi_air = sosfilt(self.sos_air, x, zi=self.zi_air)
        x = self._dbx118_above(x)

        n = min(self._fade_left, len(x))
        if n > 0:
            start = self._fade_left - n
            ramp = np.arange(start, start + n, dtype=np.float32) / float(self._fade_left)
            x[:n] *= ramp
            self._fade_left -= n

        peak = float(np.max(np.abs(x))) + 1e-8
        if peak > 0.98:
            x *= 0.98 / peak
        return np.clip(x * 32767.0, -32768, 32767).astype(np.int16)

    def _dbx118_above(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        rms = self.rms
        atk, rel = self.dbx_atk, self.dbx_rel
        t = self.dbx_thresh_db
        r1 = self.dbx_ratio - 1.0
        max_b = self.dbx_max_boost_db
        k = 8.685889638
        inv = 0.115129255
        for i, s in enumerate(x):
            p = s * s
            rms += (atk if p > rms else rel) * (p - rms)
            lev_db = k * np.log(np.sqrt(rms + 1e-12))
            delta = lev_db - t
            gdb = r1 * delta if delta > 0.0 else 0.0
            if gdb > max_b:
                gdb = max_b
            out[i] = s * np.exp(gdb * inv)
        self.rms = rms
        return out

class TerminationException(Exception):
    pass
