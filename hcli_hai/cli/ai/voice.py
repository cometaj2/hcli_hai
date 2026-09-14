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

log = logger.Logger()


class TerminationException(Exception):
    pass


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

    def stop(self, wait=True, timeout=2.0):
        """Abort the in-flight utterance from any thread.

        Signals the worker and waits for it to close its own stream.
        Does not touch the PortAudio stream object.
        """
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
            AudioDucker.restore_foreign_to_full()
            return
        done.wait(timeout=timeout)
        t.join(timeout=0.2)
        if t.is_alive():
            log.warning(
                "[ hai ] voice thread still running after stop timeout; "
                "leaving stream for the worker to close (avoids ALSA SIGSEGV)"
            )
        AudioDucker.restore_foreign_to_full()

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

            with AudioDucker(duck_to=0.25, fade_s=0.8, fade_steps=20) as ducker:
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
                        log.warning("[ hai ] speaking terminated")
                        break
                    audio_chunk = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                    if not self._write_chunk(stream, audio_chunk, cancel):
                        log.warning("[ hai ] speaking terminated")
                        break
        except TerminationException:
            log.warning("[ hai ] speaking terminated")
        except sd.PortAudioError:
            log.warning("[ hai ] port audio error. speaking terminated")
        except Exception:
            log.error(traceback.format_exc())
        finally:
            self._release_stream(stream, gen)
            done.set()
            AudioDucker.restore_foreign_to_full()

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


# Lower other apps' playback while TTS speaks.
# Linux (PipeWire/Pulse) only. Other platforms are a no-op so the
# same context-manager call site works everywhere.
class AudioDucker:
    FULL_VOLUME = 1.0
    _restore_lock = threading.Lock()

    def __init__(self, duck_to=0.25, exclude_pids=None, fade_s=0.4, fade_steps=10):
        self.duck_to = max(0.0, min(1.0, float(duck_to)))
        self.fade_s = max(0.0, float(fade_s))
        self.fade_steps = max(1, int(fade_steps))
        self.exclude_pids = {
            int(p) for p in (exclude_pids if exclude_pids is not None else [os.getpid()])
        }
        self._saved = {}
        self._targets = []
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

    def _proplist(self, si):
        return getattr(si, "proplist", None) or {}

    def _sink_pid(self, si):
        props = self._proplist(si)
        for key in ("application.process.id", "pipewire.pid"):
            raw = props.get(key)
            if raw:
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    pass
        return None

    def _is_ours(self, si, extra_pids=None):
        pids = set(self.exclude_pids)
        if extra_pids:
            pids.update(extra_pids)
        pid = self._sink_pid(si)
        return pid is not None and pid in pids

    def _target_key(self, si):
        props = self._proplist(si)
        return (
            si.index,
            props.get("application.process.id"),
            props.get("application.name"),
            props.get("media.name"),
        )

    def _matches_target(self, si, target):
        if si.index == target["index"]:
            return True
        props = self._proplist(si)
        if target.get("pid") and props.get("application.process.id") == target["pid"]:
            if not target.get("app") or props.get("application.name") == target["app"]:
                return True
        if target.get("app") and target.get("media"):
            if (props.get("application.name") == target["app"]
                    and props.get("media.name") == target["media"]):
                return True
        return False

    def _set_volume(self, pulse, si, vol):
        try:
            pulse.volume_set_all_chans(si, vol)
            return True
        except Exception as e:
            log.debug("[ hai ] volume set failed for sink-input %s: %s", si.index, e)
            return False

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
                    pulse.volume_set_all_chans(si, self.FULL_VOLUME)
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

    def _ramp(self, pulse, items, toward_full):
        if not items:
            return
        steps = self.fade_steps
        interval = self.fade_s / steps if steps else 0
        for i in range(1, steps + 1):
            t = i / float(steps)
            try:
                live = list(pulse.sink_input_list())
            except Exception:
                break
            for item in items:
                start = item["start"]
                end = self.FULL_VOLUME if toward_full else self.duck_to
                vol = start + (end - start) * t
                for si in live:
                    if self._matches_target(si, item):
                        self._set_volume(pulse, si, vol)
                        break
            if interval:
                time.sleep(interval)

    def _lift_targets(self, pulse, items, label):
        if not items:
            return 0
        try:
            live = list(pulse.sink_input_list())
        except Exception:
            return 0
        lifted = 0
        for item in items:
            for si in live:
                if getattr(si, "mute", False):
                    continue
                if self._is_ours(si):
                    continue
                if self._matches_target(si, item):
                    if self._set_volume(pulse, si, self.FULL_VOLUME):
                        lifted += 1
                    break
        if lifted:
            log.info("[ hai ] restored %d audio stream(s) to 100%% (%s)", lifted, label)
        return lifted

    @classmethod
    def restore_foreign_to_full(cls, exclude_pids=None):
        """Best-effort: put every non-hai, non-muted sink-input back at 100%.

        Safe to call from stop(), worker finally, or a CLI helper.
        Does not touch this process's TTS stream while it is still live.
        """
        if not sys.platform.startswith("linux"):
            return
        with cls._restore_lock:
            ducker = cls(exclude_pids=exclude_pids)
            pulse = ducker._linux_pulse()
            if pulse is None:
                return
            try:
                lifted = 0
                for si in pulse.sink_input_list():
                    if getattr(si, "mute", False):
                        continue
                    if ducker._is_ours(si):
                        continue
                    try:
                        current = si.volume.value_flat
                    except Exception:
                        current = None
                    if current is not None and current >= cls.FULL_VOLUME - 0.01:
                        continue
                    if ducker._set_volume(pulse, si, cls.FULL_VOLUME):
                        lifted += 1
                if lifted:
                    log.info("[ hai ] recovered %d background stream(s) to 100%%", lifted)
            except Exception as e:
                log.warning("[ hai ] background volume recovery failed: %s", e)
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
            # Recover leftovers from a previous interrupted duck before snapshotting.
            AudioDucker.restore_foreign_to_full(exclude_pids=self.exclude_pids)

            inputs = list(pulse.sink_input_list())
            known = {si.index for si in inputs}
            saved = {}
            targets = []
            for si in inputs:
                if getattr(si, "mute", False):
                    continue
                if self._is_ours(si):
                    continue
                try:
                    original = si.volume.value_flat
                except Exception:
                    continue
                props = self._proplist(si)
                target = {
                    "index": si.index,
                    "pid": props.get("application.process.id"),
                    "app": props.get("application.name"),
                    "media": props.get("media.name"),
                    "start": original,
                }
                targets.append(target)
                if original > self.duck_to:
                    saved[si.index] = original
            with self._lock:
                self._saved = saved
                self._targets = targets
                self._known = known
            self._ramp(pulse, [t for t in targets if t["start"] > self.duck_to], toward_full=False)
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
            targets = list(self._targets)
            saved = self._saved
            self._saved = {}
            self._targets = []
            self._known = set()
        pulse = self._linux_pulse()
        if pulse is None:
            AudioDucker.restore_foreign_to_full(exclude_pids=self.exclude_pids)
            return
        try:
            for item in targets:
                item["start"] = self.duck_to
            self._ramp(pulse, targets, toward_full=True)
            # Hard set to 100% so a missed fade step cannot leave them at 25%.
            self._lift_targets(pulse, targets, "ducked")
        except Exception as e:
            log.warning("[ hai ] linux unduck failed: %s", e)
        finally:
            try:
                pulse.close()
            except Exception:
                pass
            AudioDucker.restore_foreign_to_full(exclude_pids=self.exclude_pids)
