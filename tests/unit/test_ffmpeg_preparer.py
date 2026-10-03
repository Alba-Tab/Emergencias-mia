import math
import tempfile
import unittest
from pathlib import Path

from app.adapters.outbound.media.ffmpeg_preparer import (
    FfmpegPreparer, choose_frames, parse_scene_scores, parse_sound_level,
)
from app.application.ports.media_preparer import SoundLevel
from app.application.ports.media_preparer import MediaPreparationError


def uniform(count, step=0.1):
    return [round(i * step, 3) for i in range(count)]


class ChooseFramesTests(unittest.TestCase):
    def test_always_first_and_last_and_never_more_than_max(self):
        times = uniform(100)
        chosen = choose_frames(times, [0.0] * 100, 8, 0.3)
        self.assertEqual(len(chosen), 8)
        self.assertEqual((chosen[0], chosen[-1]), (0, 99))
        self.assertEqual(chosen, sorted(set(chosen)))

    def test_uniform_fill_spreads_frames(self):
        chosen = choose_frames(uniform(101), [0.0] * 101, 5, 0.3)
        self.assertEqual(chosen, [0, 25, 50, 75, 100])

    def test_scene_changes_come_first_by_score(self):
        scores = [0.0] * 100
        scores[30], scores[31], scores[70] = 0.9, 0.8, 0.5
        chosen = choose_frames(uniform(100), scores, 4, 0.3)
        # 31 queda pegado a 30 (más cerca que el hueco mínimo) y se descarta.
        self.assertEqual(chosen, [0, 30, 70, 99])
        # Con un umbral por encima de todos los puntajes solo queda el relleno uniforme.
        self.assertEqual(choose_frames(uniform(100), scores, 4, 0.95), choose_frames(uniform(100), [0.0] * 100, 4, 0.3))

    def test_short_videos(self):
        self.assertEqual(choose_frames([], [], 8, 0.3), [])
        self.assertEqual(choose_frames([0.0], [0.0], 8, 0.3), [0])
        self.assertEqual(choose_frames(uniform(3), [0.0] * 3, 8, 0.3), [0, 1, 2])

    def test_parse_scene_scores(self):
        text = ("frame:0    pts:0       pts_time:0\nlavfi.scene_score=0.000000\n"
                "frame:1    pts:512     pts_time:0.5\nlavfi.scene_score=0.731000\n")
        self.assertEqual(parse_scene_scores(text), ([0.0, 0.5], [0.0, 0.731]))


class SoundLevelTests(unittest.TestCase):
    def test_parse_volume_and_audible_seconds(self):
        # Salida real de ffmpeg 9: el primer grafo de prueba informa 0 muestras y se ignora.
        text = ("[Parsed_volumedetect_1 @ 0x1] n_samples: 0\n"
                "[Parsed_silencedetect_3 @ 0x2] silence_start: 0\n"
                "[Parsed_silencedetect_3 @ 0x2] silence_end: 1.000023 | silence_duration: 1.000023\n"
                "[Parsed_silencedetect_3 @ 0x2] silence_start: 2.000091\n"
                "[Parsed_silencedetect_3 @ 0x2] silence_end: 3.6 | silence_duration: 1.6\n"
                "[Parsed_volumedetect_1 @ 0x3] n_samples: 132300\n"
                "[Parsed_volumedetect_1 @ 0x3] mean_volume: -25.8 dB\n"
                "[Parsed_volumedetect_1 @ 0x3] max_volume: -17.7 dB\n"
                "[Parsed_volumedetect_1 @ 0x3] histogram_17db: 36\n")
        self.assertEqual(parse_sound_level(text), SoundLevel(-17.7, -25.8, 1.0))

    def test_sound_from_the_start_and_trailing_silence_without_end(self):
        text = ("[Parsed_silencedetect_3 @ 0x2] silence_start: 2.5\n"
                "[Parsed_volumedetect_1 @ 0x3] mean_volume: -40 dB\n"
                "[Parsed_volumedetect_1 @ 0x3] max_volume: -20 dB\n")
        self.assertEqual(parse_sound_level(text).audible_seconds, 2.5)

    def test_no_samples_is_silence(self):
        level = parse_sound_level("[Parsed_volumedetect_1 @ 0x1] n_samples: 0\n")
        self.assertEqual((level.max_volume_db, level.audible_seconds), (-math.inf, 0.0))


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_tool_and_timeout_are_preparation_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MediaPreparationError):
                await FfmpegPreparer()._run(Path(directory), "/nonexistent/ffprobe")
            with self.assertLogs("app", level="WARNING"), self.assertRaises(MediaPreparationError):
                await FfmpegPreparer(timeout=0.1)._run(Path(directory), "sleep", "5")

    async def test_can_read_stderr_instead_of_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            out = await FfmpegPreparer()._run(Path(directory), "sh", "-c", "echo fuera; echo error >&2", stderr=True)
        self.assertEqual(out, b"error\n")
