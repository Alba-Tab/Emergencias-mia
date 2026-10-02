"""Validación de URLs de descarga entregadas por el backend."""

import socket
from ipaddress import ip_address
from urllib.parse import urlsplit


def _is_ip_literal(host: str) -> bool:
    """Incluye las formas que el resolvedor acepta como IPv4: `2130706433`, `0x7f.1` o `127.1`."""
    try:
        ip_address(host)
        return True
    except ValueError:
        pass
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        return False


def valid_download_url(url: str) -> bool:
    """Acepta HTTPS público; la red de despliegue debe limitar destinos salientes."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443) or parsed.fragment:
            return False
        # `localhost.` resuelve igual que `localhost`.
        host = host.lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            return False
        # Un nombre sin punto (`backend`, `postgres`) es un servicio de la red interna, no un almacén público.
        if "." not in host:
            return False
        return not _is_ip_literal(host)
    except ValueError:
        return False
