import io
import threading
import wave
import random
from pathlib import Path

import numpy as np
from piper.voice import PiperVoice, SynthesisConfig
from scipy.signal import butter, sosfilt, sosfilt_zi, tf2sos

import config as c


def _rbj_peaking_sos(sr, f0, q, gain_db):
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / sr
    cosw = np.cos(w0)
    sinw = np.sin(w0)
    alpha = sinw / (2.0 * q)

    b0 = 1.0 + alpha * a
    b1 = -2.0 * cosw
    b2 = 1.0 - alpha * a

    a0 = 1.0 + alpha / a
    a1 = -2.0 * cosw
    a2 = 1.0 - alpha / a

    return tf2sos(
        [
            b0 / a0,
            b1 / a0,
            b2 / a0,
        ],
        [
            1.0,
            a1 / a0,
            a2 / a0,
        ],
    )


def _rbj_lowshelf_sos(sr, f0, q, gain_db):
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / sr
    cosw = np.cos(w0)
    sinw = np.sin(w0)
    alpha = sinw / (2.0 * q)
    two_sa = 2.0 * np.sqrt(a) * alpha

    b0 = a * (
        (a + 1.0)
        - (a - 1.0) * cosw
        + two_sa
    )

    b1 = 2.0 * a * (
        (a - 1.0)
        - (a + 1.0) * cosw
    )

    b2 = a * (
        (a + 1.0)
        - (a - 1.0) * cosw
        - two_sa
    )

    a0 = (
        (a + 1.0)
        + (a - 1.0) * cosw
        + two_sa
    )

    a1 = -2.0 * (
        (a - 1.0)
        + (a + 1.0) * cosw
    )

    a2 = (
        (a + 1.0)
        + (a - 1.0) * cosw
        - two_sa
    )

    return tf2sos(
        [
            b0 / a0,
            b1 / a0,
            b2 / a0,
        ],
        [
            1.0,
            a1 / a0,
            a2 / a0,
        ],
    )


def _rbj_highshelf_sos(sr, f0, q, gain_db):
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / sr
    cosw = np.cos(w0)
    sinw = np.sin(w0)
    alpha = sinw / (2.0 * q)
    two_sa = 2.0 * np.sqrt(a) * alpha

    b0 = a * (
        (a + 1.0)
        + (a - 1.0) * cosw
        + two_sa
    )

    b1 = -2.0 * a * (
        (a - 1.0)
        + (a + 1.0) * cosw
    )

    b2 = a * (
        (a + 1.0)
        + (a - 1.0) * cosw
        - two_sa
    )

    a0 = (
        (a + 1.0)
        - (a - 1.0) * cosw
        + two_sa
    )

    a1 = 2.0 * (
        (a - 1.0)
        - (a + 1.0) * cosw
    )

    a2 = (
        (a + 1.0)
        - (a - 1.0) * cosw
        - two_sa
    )

    return tf2sos(
        [
            b0 / a0,
            b1 / a0,
            b2 / a0,
        ],
        [
            1.0,
            a1 / a0,
            a2 / a0,
        ],
    )


class VoiceSynthesizer:
    """
    Server-side Piper text-to-WAV synthesizer.

    Pipeline:

        text
          -> Piper
          -> VoiceSmoother
          -> VoiceEnhancer
          -> mono 16-bit PCM WAV

    This class does not play audio.
    """

    def __init__(self, model_path=None):
        self.config = c.Config()

        self.model_path = (
            model_path
            or self.config.assistant_tts_path
        )

        self.voice = None
        self.sample_rate = None

        self._voice_lock = threading.RLock()
        self._synthesis_lock = threading.RLock()

    def _ensure_voice(self):
        with self._voice_lock:
            if self.voice is not None:
                return

            if not self.model_path:
                raise RuntimeError(
                    "No assistant TTS model path configured"
                )

            self.voice = PiperVoice.load(self.model_path)
            self.sample_rate = self.voice.config.sample_rate

    def make_silence(self, sample_rate, duration_s):
        frames = int(sample_rate * duration_s)

        return np.zeros(
            frames,
            dtype=np.int16,
        ).tobytes()

    def pcm_to_wav(self, pcm_data):
        output = io.BytesIO()

        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(pcm_data)

        return output.getvalue()

    def synthesize(self, text, cancel_event=None):
        """
        Return a complete WAV file as bytes.
        """

        if not text or not text.strip():
            return b""

        self._ensure_voice()

        # Both processors maintain state between chunks.
        # They must be local to this synthesis operation.
        enhancer = VoiceEnhancer(self.sample_rate)

        output = io.BytesIO()

        syn = SynthesisConfig(
            length_scale=1.20,
            noise_scale=0.28,
            noise_w_scale=0.35,
            normalize_audio=True,
        )

        # Piper synthesis is serialized for this instance.
        with self._synthesis_lock:
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)  # signed 16-bit PCM
                wav.setframerate(self.sample_rate)

                chunks = self.voice.synthesize(
                    text,
                    syn_config=syn,
                )

                for chunk in chunks:
                    if (
                        cancel_event is not None
                        and cancel_event.is_set()
                    ):
                        raise SynthesisCancelled()

                    audio = np.frombuffer(
                        chunk.audio_int16_bytes,
                        dtype=np.int16,
                    )

                    if audio.size == 0:
                        continue

                    # Apply smoothing first, then enhancement.
                    audio = enhancer.process(audio)

                    wav.writeframes(audio.tobytes())

        return output.getvalue()

    def iter_pcm(self, text, cancel_event=None):
        """
        Yield processed signed 16-bit mono PCM chunks as they are generated.

        Each yielded value is raw PCM audio, not a WAV file.
        """

        if not text or not text.strip():
            return

        self._ensure_voice()

        enhancer = VoiceEnhancer(self.sample_rate)

        syn = SynthesisConfig(
            length_scale=1.20,
            noise_scale=0.28,
            noise_w_scale=0.35,
            normalize_audio=True,
        )

        with self._synthesis_lock:
            for chunk in self.voice.synthesize(
                text,
                syn_config=syn,
            ):
                if (
                    cancel_event is not None
                    and cancel_event.is_set()
                ):
                    raise SynthesisCancelled()

                audio = np.frombuffer(
                    chunk.audio_int16_bytes,
                    dtype=np.int16,
                )

                if audio.size == 0:
                    continue

                audio = enhancer.process(audio)

                # Ensure the bytes are safe to send directly.
                if not audio.flags.c_contiguous:
                    audio = np.ascontiguousarray(audio)

                yield audio.tobytes()

                silence = random.uniform(0.75, 1.2)
                yield self.make_silence(self.sample_rate, silence)

    def get_sample_rate(self):
        self._ensure_voice()
        return self.sample_rate


class VoiceEnhancer:
    """
    Speech EQ, mild dynamic gain, fade-in, and peak limiting.

    This object is stateful and must be created separately for each
    synthesis operation.
    """

    def __init__(self, sample_rate):
        sample_rate = float(sample_rate)

        def zero_state(sos):
            return np.zeros_like(sosfilt_zi(sos))

        self.sos_hp = butter(
            2,
            70.0 / (sample_rate / 2.0),
            btype="highpass",
            output="sos",
        )
        self.zi_hp = zero_state(self.sos_hp)

        self.sos_body = _rbj_lowshelf_sos(
            sample_rate,
            f0=170.0,
            q=0.70,
            gain_db=+2.0,
        )
        self.zi_body = zero_state(self.sos_body)

        self.sos_box = _rbj_peaking_sos(
            sample_rate,
            f0=480.0,
            q=1.10,
            gain_db=-1.2,
        )
        self.zi_box = zero_state(self.sos_box)

        self.sos_pres = _rbj_peaking_sos(
            sample_rate,
            f0=2600.0,
            q=0.90,
            gain_db=+1.8,
        )
        self.zi_pres = zero_state(self.sos_pres)

        self.sos_air = _rbj_highshelf_sos(
            sample_rate,
            f0=6500.0,
            q=0.70,
            gain_db=-2.5,
        )
        self.zi_air = zero_state(self.sos_air)

        self.rms = 1e-6

        self.dbx_thresh_db = -12.0
        self.dbx_ratio = 1.12
        self.dbx_max_boost_db = 2.5

        self.dbx_atk = 1.0 - np.exp(
            -1.0 / (0.008 * sample_rate)
        )

        self.dbx_rel = 1.0 - np.exp(
            -1.0 / (0.120 * sample_rate)
        )

        self.fade_left = int(0.008 * sample_rate)

    def process(self, audio_i16):
        if audio_i16.size == 0:
            return audio_i16

        x = audio_i16.astype(np.float32) / 32768.0

        x, self.zi_hp = sosfilt(
            self.sos_hp,
            x,
            zi=self.zi_hp,
        )

        x, self.zi_body = sosfilt(
            self.sos_body,
            x,
            zi=self.zi_body,
        )

        x, self.zi_box = sosfilt(
            self.sos_box,
            x,
            zi=self.zi_box,
        )

        x, self.zi_pres = sosfilt(
            self.sos_pres,
            x,
            zi=self.zi_pres,
        )

        x, self.zi_air = sosfilt(
            self.sos_air,
            x,
            zi=self.zi_air,
        )

        x = self._dbx118_above(x)

        count = min(self.fade_left, len(x))

        if count > 0:
            start = self.fade_left - count

            ramp = np.arange(
                start,
                start + count,
                dtype=np.float32,
            ) / float(self.fade_left)

            x[:count] *= ramp
            self.fade_left -= count

        peak = float(np.max(np.abs(x))) + 1e-8

        if peak > 0.98:
            x *= 0.98 / peak

        return np.clip(
            x * 32767.0,
            -32768,
            32767,
        ).astype(np.int16)

    def _dbx118_above(self, x):
        output = np.empty_like(x)

        rms = self.rms
        attack = self.dbx_atk
        release = self.dbx_rel
        threshold = self.dbx_thresh_db
        ratio_minus_one = self.dbx_ratio - 1.0
        max_boost = self.dbx_max_boost_db

        db_scale = 8.685889638
        linear_scale = 0.115129255

        for index, sample in enumerate(x):
            power = sample * sample

            rms += (
                attack if power > rms else release
            ) * (power - rms)

            level_db = db_scale * np.log(
                np.sqrt(rms + 1e-12)
            )

            delta = level_db - threshold

            if delta > 0.0:
                gain_db = ratio_minus_one * delta
            else:
                gain_db = 0.0

            gain_db = min(gain_db, max_boost)

            output[index] = sample * np.exp(
                gain_db * linear_scale
            )

        self.rms = rms
        return output

class SynthesisCancelled(Exception):
    pass


