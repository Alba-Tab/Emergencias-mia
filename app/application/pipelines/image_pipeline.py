"""Reglas exclusivas del análisis de imágenes."""

SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def validate_image_type(mime_type: str) -> None:
    if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        raise ValueError("Tipo de imagen no admitido")
