"""Prueba con ffmpeg real sobre videos sintéticos generados en un directorio temporal.

Se omite si ffmpeg o ffprobe no están instalados (`brew install ffmpeg` en macOS).
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.adapters.outbound.media.ffmpeg_preparer import FfmpegPreparer
from app.domain.errors import AiError

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def ffmpeg(directory: Path, name: str, *args: str) -> bytes:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args, name], cwd=directory, check=True, timeout=60)
    return (directory / name).read_bytes()


def jpeg_size(directory: Path, data: bytes) -> tuple[int, int]:
    (directory / "frame.jpg").write_bytes(data)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json", "frame.jpg"],
                         cwd=directory, check=True, capture_output=True, timeout=30).stdout
    stream = json.loads(out)["streams"][0]
    return stream["width"], stream["height"]


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe no están instalados")
class FfmpegPreparerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._dir.name)
        # 4 s a 1920x1080: azul y luego rojo (cambio de escena en el segundo 2) con un tono de 440 Hz.
        cls.mp4 = ffmpeg(cls.dir, "escena.mp4", "-filter_complex",
                         "color=c=blue:s=1920x1080:r=25:d=2[a];color=c=red:s=1920x1080:r=25:d=2[b];"
                         "[a][b]concat=n=2:v=1:a=0[v];sine=frequency=440:duration=4[s]",
                         "-map", "[v]", "-map", "[s]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                         "-shortest")
        cls.silent = ffmpeg(cls.dir, "mudo.mp4", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p")
        # WebM escrito a un pipe: sin duración en la cabecera, como el de un navegador.
        cls.webm = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
             "-c:v", "libvpx", "-f", "webm", "pipe:1"], check=True, capture_output=True, timeout=60).stdout

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def setUp(self):
        self.preparer = FfmpegPreparer(timeout=30)

    async def test_video_frames_seconds_duration_and_audio(self):
        info = await self.preparer.probe(self.mp4, "video/mp4")
        self.assertAlmostEqual(info.duration, 4.0, delta=0.1)
        self.assertTrue(info.has_video and info.has_audio)

        prepared = await self.preparer.prepare_video(self.mp4, "video/mp4", info, 8, 0.3)
        seconds = [frame.second for frame in prepared.frames]
        self.assertLessEqual(len(seconds), 8)
        self.assertEqual(seconds, sorted(seconds))
        self.assertEqual(seconds[0], 0.0)
        self.assertGreaterEqual(seconds[-1], 3.9)
        self.assertIn(2.0, seconds)  # el cambio de escena
        for frame in prepared.frames:
            self.assertTrue(frame.data.startswith(b"\xff\xd8"))
        self.assertEqual(jpeg_size(self.dir, prepared.frames[0].data), (1280, 720))

        self.assertEqual(prepared.audio[4:12], b"ftypM4A ")
        audio_info = await self.preparer.probe(prepared.audio, "audio/mp4")
        self.assertAlmostEqual(audio_info.duration, 4.0, delta=0.2)
        self.assertFalse(audio_info.has_video)

    async def test_scene_change_is_kept_with_few_frames(self):
        info = await self.preparer.probe(self.mp4, "video/mp4")
        prepared = await self.preparer.prepare_video(self.mp4, "video/mp4", info, 3, 0.3)
        self.assertEqual([frame.second for frame in prepared.frames][:2], [0.0, 2.0])
        self.assertEqual(len(prepared.frames), 3)

    async def test_video_without_audio(self):
        info = await self.preparer.probe(self.silent, "video/mp4")
        self.assertFalse(info.has_audio)
        prepared = await self.preparer.prepare_video(self.silent, "video/mp4", info, 8, 0.3)
        self.assertIsNone(prepared.audio)
        self.assertEqual(len(prepared.frames), 8)
        self.assertEqual(jpeg_size(self.dir, prepared.frames[0].data), (320, 240))  # no se agranda

    async def test_duration_without_header_is_measured_from_packets(self):
        info = await self.preparer.probe(self.webm, "video/webm")
        self.assertAlmostEqual(info.duration, 3.0, delta=0.15)

    async def test_corrupt_media_is_unreadable(self):
        for data, mime in ((b"\x1a\x45\xdf\xa3" + bytes(64), "video/webm"), (self.mp4[:2000], "video/mp4"),
                           (b"ID3" + bytes(64), "audio/mpeg")):
            with self.assertRaises(AiError) as caught:
                await self.preparer.probe(data, mime)
            self.assertEqual(caught.exception.code, "unreadable_media", mime)

    async def test_temporary_files_are_always_removed(self):
        with tempfile.TemporaryDirectory() as scratch, patch.object(tempfile, "tempdir", scratch):
            info = await self.preparer.probe(self.mp4, "video/mp4")
            await self.preparer.prepare_video(self.mp4, "video/mp4", info, 4, 0.3)
            with self.assertRaises(AiError):
                await self.preparer.probe(b"\x1a\x45\xdf\xa3" + bytes(64), "video/webm")
            self.assertEqual(list(Path(scratch).iterdir()), [])
