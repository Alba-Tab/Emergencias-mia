"""Reglas exclusivas del análisis de imágenes."""

SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def validate_image_type(mime_type: str) -> None:
    if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        raise ValueError("Tipo de imagen no admitido")


def validate_image_bytes(data: bytes, mime_type: str) -> None:
    validate_image_type(mime_type)
    signatures = {
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not signatures[mime_type]:
        raise ValueError("Los bytes no coinciden con el MIME declarado")
