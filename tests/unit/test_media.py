import unittest
from hashlib import sha256

from app.application.pipelines.media import (
    AUDIO_MIME_TYPES, IMAGE_MIME_TYPES, VIDEO_MIME_TYPES, MediaPolicy, bmff_duration_seconds, modality_of,
    wav_duration_seconds,
)
from app.domain.errors import AiError
from app.domain.evidence_reference import Modality
from tests.fixtures import synthetic_bmff, synthetic_png, synthetic_wav


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


class DurationTests(unittest.TestCase):
    def test_bmff_versions_and_wav(self):
        self.assertAlmostEqual(bmff_duration_seconds(synthetic_bmff(12.5)), 12.5)
        self.assertAlmostEqual(bmff_duration_seconds(synthetic_bmff(90, version=1)), 90)
        self.assertAlmostEqual(wav_duration_seconds(synthetic_wav(2)), 2)

    def test_truncated_or_missing_moov(self):
        self.assertIsNone(bmff_duration_seconds(synthetic_bmff(5)[:20]))
        self.assertIsNone(bmff_duration_seconds(b"\x00\x00\x00\x08ftyp"))
        self.assertIsNone(wav_duration_seconds(synthetic_wav(1)[:24]))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.image = MediaPolicy(Modality.IMAGE, IMAGE_MIME_TYPES, 1024)
        self.audio = MediaPolicy(Modality.AUDIO, AUDIO_MIME_TYPES, 1024 * 1024, 60)
        self.video = MediaPolicy(Modality.VIDEO, VIDEO_MIME_TYPES, 1024 * 1024, 30)

    def assert_code(self, code, policy, data, mime, checksum=None):
        with self.assertRaises(AiError) as caught:
            policy.validate(data, mime, checksum or digest(data))
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(caught.exception.retryable)

    def test_accepts_valid_media(self):
        self.image.validate(synthetic_png(), "image/png", digest(synthetic_png()))
        m4a = synthetic_bmff(30, b"M4A ")
        self.audio.validate(m4a, "audio/mp4", digest(m4a))
        self.audio.validate(synthetic_wav(1), "audio/wav", digest(synthetic_wav(1)))
        mov = synthetic_bmff(10, b"qt  ")
        self.video.validate(mov, "video/quicktime", digest(mov))
        webm = b"\x1a\x45\xdf\xa3" + bytes(32)
        self.video.validate(webm, "video/webm", digest(webm))

    def test_rejections_in_order(self):
        png = synthetic_png()
        self.assert_code("unsupported_media_type", self.image, png, "image/gif")
        self.assert_code("evidence_empty", self.image, b"", "image/png")
        self.assert_code("evidence_too_large", MediaPolicy(Modality.IMAGE, IMAGE_MIME_TYPES, 10), png, "image/png")
        self.assert_code("mime_mismatch", self.image, png, "image/jpeg")
        self.assert_code("checksum_mismatch", self.image, png, "image/png", "0" * 64)
        self.assert_code("duration_exceeded", self.audio, synthetic_bmff(61), "audio/mp4")
        self.assert_code("duration_exceeded", self.audio, synthetic_wav(61, byte_rate=100), "audio/wav")
        self.assert_code("unreadable_media", self.video, synthetic_bmff(5)[:40], "video/mp4")
        self.assert_code("unreadable_media", self.audio, synthetic_wav(1)[:24], "audio/wav")

    def test_modality_of(self):
        self.assertIs(modality_of("image/webp"), Modality.IMAGE)
        self.assertIs(modality_of("audio/x-m4a"), Modality.AUDIO)
        self.assertIs(modality_of("video/quicktime"), Modality.VIDEO)
        with self.assertRaises(AiError):
            modality_of("application/pdf")
