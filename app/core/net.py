"""Validación de URLs de descarga entregadas por el backend."""

from ipaddress import ip_address
from urllib.parse import urlsplit


def valid_download_url(url: str) -> bool:
    """Acepta HTTPS público; la red de despliegue debe limitar destinos salientes."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443) or parsed.fragment:
            return False
        if host.lower() == "localhost" or host.lower().endswith(".localhost"):
            return False
        try:
            ip_address(host)
        except ValueError:
            return True
        return False
    except ValueError:
        return False
