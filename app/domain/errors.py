"""Error de negocio con un código estable que el backend puede interpretar."""


class AiError(Exception):
    """`retryable` indica si repetir la misma solicitud más tarde puede funcionar."""

    def __init__(self, code: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
