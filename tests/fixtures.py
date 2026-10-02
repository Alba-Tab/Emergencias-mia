"""Medios sintéticos mínimos, sin datos personales, y respuestas de modelo de ejemplo."""

import struct
import zlib


def synthetic_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00")
    return header + chunk(b"IHDR", ihdr) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


def synthetic_bmff(seconds: float, brand: bytes = b"isom", version: int = 0) -> bytes:
    """Contenedor ISO BMFF (MP4/M4A/MOV) con solo `ftyp` y `moov/mvhd`."""
    timescale = 1000
    if version == 1:
        mvhd = bytes([1, 0, 0, 0]) + struct.pack(">QQIQ", 0, 0, timescale, int(seconds * timescale))
    else:
        mvhd = bytes(4) + struct.pack(">IIII", 0, 0, timescale, int(seconds * timescale))
    mvhd += bytes(80)
    return _box(b"ftyp", brand + bytes(4) + brand) + _box(b"moov", _box(b"mvhd", mvhd)) + _box(b"mdat", b"\x00" * 16)


def synthetic_wav(seconds: float, byte_rate: int = 16000) -> bytes:
    data = b"\x00" * int(seconds * byte_rate)
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, byte_rate, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def evidence_output(**overrides) -> dict:
    data = {
        "summary": "Choque entre dos autos en una intersección.",
        "eventType": "traffic_accident",
        "peopleMin": 1,
        "peopleMax": 2,
        "hazards": ["traffic"],
        "observations": [
            {"text": "Dos autos con daños frontales.", "basis": "observed"},
            {"text": "Una persona podría seguir dentro de un vehículo.", "basis": "inferred"},
        ],
        "risks": ["Tráfico circulando cerca de los vehículos."],
        "severity": "moderate",
        "severityBasis": ["Daños frontales visibles en ambos autos."],
        "limitations": ["El interior de los vehículos no es visible."],
    }
    data.update(overrides)
    return data


def analysis_payload(**overrides) -> dict:
    """Objeto `analysis` tal como lo devuelve `POST /v1/analyses`."""
    data = {
        "summary": "Choque entre dos autos.",
        "eventType": "traffic_accident",
        "people": {"min": 1, "max": 2},
        "hazards": ["traffic"],
        "observations": [{"text": "Dos autos dañados.", "basis": "observed"}],
        "risks": ["Tráfico cercano."],
        "severity": {"level": "moderate", "basis": ["Daños visibles."]},
        "limitations": ["Interior no visible."],
        "transcript": None,
        "timeline": [],
    }
    data.update(overrides)
    return data
