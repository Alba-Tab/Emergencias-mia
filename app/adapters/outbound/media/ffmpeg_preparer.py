"""Adaptador de `MediaPreparer` con ffprobe y ffmpeg como procesos externos.

Cada proceso trabaja sobre una copia del archivo en un directorio temporal que siempre se borra,
lee solo archivos locales (`-protocol_whitelist file`), con el formato de entrada fijado por el MIME,
límites de sondeo y un timeout propio. Nunca se registra el contenido ni la salida de las herramientas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import tempfile
from pathlib import Path

from app.application.ports.media_preparer import Frame, MediaInfo, MediaPreparationError, PreparedVideo
from app.domain.errors import AiError

logger = logging.getLogger(__name__)

# Demuxer de ffmpeg por MIME: el formato no se adivina a partir del contenido.
DEMUXERS = {
    "audio/mp4": "mov", "audio/m4a": "mov", "audio/x-m4a": "mov", "audio/mpeg": "mp3", "audio/aac": "aac",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg",
    "video/mp4": "mov", "video/quicktime": "mov", "video/webm": "matroska",
}
PROBE_LIMITS = ["-probesize", "10000000", "-analyzeduration", "10000000"]
INPUT_NAME = "entrada"
SCENE_WIDTH = 320
FRAME_MAX_SIDE = 1280


def _input_args(mime_type: str) -> list[str]:
    demuxer = DEMUXERS.get(mime_type)
    if demuxer is None:
        raise AiError("unsupported_media_type")
    return ["-protocol_whitelist", "file", *PROBE_LIMITS, "-f", demuxer, "-i", INPUT_NAME]


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def choose_frames(times: list[float], scores: list[float], max_frames: int, threshold: float) -> list[int]:
    """Índices de los fotogramas a enviar: el primero, el último, cambios de escena y relleno uniforme.

    Los cambios de escena entran de mayor a menor puntaje sin quedar pegados a otro elegido; el
    resto se reparte partiendo siempre el mayor hueco de tiempo entre dos elegidos.
    """
    count = len(times)
    if count == 0 or max_frames <= 0:
        return []
    chosen = {0, count - 1} if max_frames >= 2 else {0}
    span = times[-1] - times[0]
    min_gap = span / (max_frames * 2) if span > 0 else 0

    def far_enough(index: int) -> bool:
        return all(abs(times[index] - times[other]) >= min_gap for other in chosen)

    scene_changes = sorted((i for i in range(count) if scores[i] > threshold), key=lambda i: (-scores[i], i))
    for index in scene_changes:
        if len(chosen) >= max_frames:
            break
        if index not in chosen and far_enough(index):
            chosen.add(index)

    while len(chosen) < max_frames:
        ordered = sorted(chosen)
        best = None
        for left, right in zip(ordered, ordered[1:]):
            if right - left < 2:
                continue
            gap = times[right] - times[left]
            if best is None or gap > best[0]:
                best = (gap, left, right)
        if best is None:
            break
        _, left, right = best
        middle = (times[left] + times[right]) / 2
        chosen.add(min(range(left + 1, right), key=lambda i: (abs(times[i] - middle), i)))
    return sorted(chosen)


def parse_scene_scores(text: str) -> tuple[list[float], list[float]]:
    """Lee la salida de `metadata=print`: un `pts_time` por fotograma y su `lavfi.scene_score`."""
    times: list[float] = []
    scores: list[float] = []
    for line in text.splitlines():
        if line.startswith("frame:"):
            pts = next((part.split(":", 1)[1] for part in line.split() if part.startswith("pts_time:")), None)
            times.append(_number(pts) or 0.0)
            scores.append(0.0)
        elif line.startswith("lavfi.scene_score=") and scores:
            scores[-1] = _number(line.split("=", 1)[1]) or 0.0
    return times, scores


class FfmpegPreparer:
    def __init__(
        self, timeout: float = 10.0, max_concurrent: int = 4, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
    ) -> None:
        self.timeout = timeout
        self.limiter = asyncio.Semaphore(max_concurrent)
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    async def _run(self, workdir: Path, *args: str) -> bytes:
        """Ejecuta una herramienta sin shell ni stdin; devuelve stdout o falla sin exponer la salida."""
        try:
            process = await asyncio.create_subprocess_exec(
                *args, cwd=workdir, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise MediaPreparationError("herramienta no disponible") from exc
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), self.timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            logger.warning("%s superó el timeout de %.0f s", Path(args[0]).name, self.timeout)
            raise MediaPreparationError("timeout") from exc
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            raise _ToolFailed(process.returncode)
        return stdout

    def _ffmpeg(self, *args: str) -> list[str]:
        return [self.ffmpeg, "-nostdin", "-hide_banner", "-v", "error", *args]

    async def _probe(self, workdir: Path, mime_type: str) -> MediaInfo:
        command = [self.ffprobe, "-hide_banner", "-v", "error", *_input_args(mime_type),
                   "-show_entries", "format=duration,start_time:stream=codec_type,duration", "-of", "json"]
        try:
            raw = await self._run(workdir, *command)
            document = json.loads(raw)
        except (_ToolFailed, ValueError) as exc:
            raise AiError("unreadable_media") from exc
        streams = [s for s in document.get("streams") or [] if isinstance(s, dict)]
        kinds = {s.get("codec_type") for s in streams}
        media_format = document.get("format") or {}
        duration = _number(media_format.get("duration"))
        if duration is None:
            durations = [d for d in (_number(s.get("duration")) for s in streams) if d is not None]
            duration = max(durations, default=None)
        if duration is None and kinds & {"audio", "video"}:
            duration = await self._duration_from_packets(workdir, mime_type)
        if not kinds & {"audio", "video"}:
            raise AiError("unreadable_media")
        return MediaInfo(duration, "video" in kinds, "audio" in kinds, _number(media_format.get("start_time")) or 0.0)

    async def _duration_from_packets(self, workdir: Path, mime_type: str) -> float | None:
        """Contenedores sin duración declarada (p. ej. WebM de un navegador): se recorren los paquetes."""
        command = [self.ffprobe, "-hide_banner", "-v", "error", *_input_args(mime_type),
                   "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0"]
        try:
            raw = await self._run(workdir, *command)
        except _ToolFailed as exc:
            raise AiError("unreadable_media") from exc
        ends = []
        for line in raw.decode("ascii", "replace").splitlines():
            start, _, length = line.partition(",")
            if _number(start) is not None:
                ends.append(_number(start) + (_number(length) or 0.0))
        return max(ends, default=None)

    async def probe(self, data: bytes, mime_type: str) -> MediaInfo:
        async with self.limiter:
            with tempfile.TemporaryDirectory(prefix="mia-") as directory:
                workdir = Path(directory)
                await asyncio.to_thread((workdir / INPUT_NAME).write_bytes, data)
                return await self._probe(workdir, mime_type)

    async def prepare_video(
        self, data: bytes, mime_type: str, info: MediaInfo, max_frames: int, scene_threshold: float,
    ) -> PreparedVideo:
        async with self.limiter:
            with tempfile.TemporaryDirectory(prefix="mia-") as directory:
                workdir = Path(directory)
                await asyncio.to_thread((workdir / INPUT_NAME).write_bytes, data)
                try:
                    frames, audio = await _together(
                        self._frames(workdir, mime_type, max_frames, scene_threshold, info.start_time),
                        self._audio(workdir, mime_type) if info.has_audio else _nothing(),
                    )
                except (_ToolFailed, AiError) as exc:
                    raise MediaPreparationError("no se pudo preparar el video") from exc
        if not frames:
            raise MediaPreparationError("el video no tiene fotogramas")
        duration = info.duration if info.duration is not None else frames[-1].second
        return PreparedVideo(duration=duration, frames=tuple(frames), audio=audio)

    async def _frames(
        self, workdir: Path, mime_type: str, max_frames: int, threshold: float, start_time: float,
    ) -> list[Frame]:
        # Primera pasada en baja resolución: tiempo y puntaje de cambio de escena de cada fotograma.
        await self._run(workdir, *self._ffmpeg(
            *_input_args(mime_type), "-an", "-sn", "-dn",
            "-vf", f"scale={SCENE_WIDTH}:-2,select='gte(scene\\,0)',metadata=mode=print:file=escenas.txt",
            "-fps_mode", "passthrough", "-f", "null", "-",
        ))
        text = await asyncio.to_thread((workdir / "escenas.txt").read_text, "ascii", "replace")
        times, scores = parse_scene_scores(text)
        indices = choose_frames(times, scores, max_frames, threshold)
        if not indices:
            return []
        # Segunda pasada: solo los fotogramas elegidos, con el lado mayor acotado y sin agrandar.
        selection = "+".join(f"eq(n\\,{index})" for index in indices)
        scale = (f"scale=w='min({FRAME_MAX_SIDE},iw)':h='min({FRAME_MAX_SIDE},ih)'"
                 ":force_original_aspect_ratio=decrease")
        await self._run(workdir, *self._ffmpeg(
            *_input_args(mime_type), "-an", "-sn", "-dn",
            "-vf", f"select='{selection}',{scale}", "-fps_mode", "passthrough",
            "-frames:v", str(len(indices)), "-q:v", "4", "fotograma_%02d.jpg",
        ))
        frames = []
        for position, index in enumerate(indices, start=1):
            path = workdir / f"fotograma_{position:02d}.jpg"
            if not path.exists():
                break
            data = await asyncio.to_thread(path.read_bytes)
            frames.append(Frame(round(max(0.0, times[index] - start_time), 3), data))
        return frames

    async def _audio(self, workdir: Path, mime_type: str) -> bytes | None:
        await self._run(workdir, *self._ffmpeg(
            *_input_args(mime_type), "-vn", "-sn", "-dn", "-map", "0:a:0",
            "-ac", "1", "-c:a", "aac", "-b:a", "48k", "-f", "ipod", "audio.m4a",
        ))
        path = workdir / "audio.m4a"
        return await asyncio.to_thread(path.read_bytes) if path.exists() else None


async def _nothing() -> None:
    return None


async def _together(*coroutines):
    """Como `gather`, pero si una falla cancela las demás y espera a que terminen sus procesos.

    Así ningún ffmpeg sigue escribiendo cuando se borra el directorio temporal.
    """
    tasks = [asyncio.ensure_future(coroutine) for coroutine in coroutines]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return [task.result() for task in tasks]


class _ToolFailed(Exception):
    def __init__(self, returncode: int | None) -> None:
        super().__init__(f"código de salida {returncode}")
