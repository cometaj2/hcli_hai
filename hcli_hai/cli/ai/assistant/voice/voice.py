import threading
import traceback

from ai.assistant.voice import synthesizer as s


class Voice:
    def __init__(self):
        self.synth = s.VoiceSynthesizer()

        self._lock = threading.RLock()
        self._thread = None
        self._cancel = None
        self._generation = 0

    def speak(self, message):
        if not message:
            return

        self.stop()

        cancel = threading.Event()

        with self._lock:
            self._generation += 1
            generation = self._generation
            self._cancel = cancel

            return self._speak_worker(message, cancel, generation)

    def _speak_worker(self, message, cancel, generation):
        try:
            # This loads Piper if necessary and obtains the actual output
            # sample rate before the player opens its sounddevice stream.
            sample_rate = self.synth.get_sample_rate()

            if self._is_cancelled(cancel, generation):
                return

            # iter_pcm() yields processed int16 PCM chunks as Piper produces
            # them. Each chunk is immediately sent to the player.
            for pcm_data in self.synth.iter_pcm(message, cancel_event=cancel):
                if self._is_cancelled(cancel, generation):
                    return

                yield pcm_data

        except s.SynthesisCancelled:
            pass

        except Exception:
            traceback.print_exc()

        finally:
            with self._lock:
                if self._generation == generation:
                    self._thread = None
                    self._cancel = None

    def _is_current(self, generation):
        with self._lock:
            return self._generation == generation

    def _is_cancelled(self, cancel, generation):
        return (cancel.is_set() or not self._is_current(generation))

    def stop(self):
        with self._lock:
            self._generation += 1

            cancel = self._cancel
            thread = self._thread

            self._cancel = None
            self._thread = None

        if cancel is not None:
            cancel.set()

        if (thread is not None and thread is not threading.current_thread()):
            thread.join(timeout=0.2)
