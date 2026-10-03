import tempfile
import unittest
from pathlib import Path

from app.adapters.outbound.media.ffmpeg_preparer import FfmpegPreparer, choose_frames, parse_scene_scores
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


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_tool_and_timeout_are_preparation_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MediaPreparationError):
                await FfmpegPreparer()._run(Path(directory), "/nonexistent/ffprobe")
            with self.assertLogs("app", level="WARNING"), self.assertRaises(MediaPreparationError):
                await FfmpegPreparer(timeout=0.1)._run(Path(directory), "sleep", "5")
