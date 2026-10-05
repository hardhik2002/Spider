import ipaddress
from urllib.parse import urljoin, urlsplit, urlunsplit


class InvalidURL(ValueError):
    pass


def normalize_url(url: str, base_url: str | None = None) -> str:
    try:
        absolute = urljoin(base_url, url.strip()) if base_url else url.strip()
        parts = urlsplit(absolute)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname:
            raise InvalidURL("Only absolute HTTP/HTTPS URLs are supported")
        if parts.username or parts.password:
            raise InvalidURL("URLs with credentials are unsupported")
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not host or any(char.isspace() for char in host):
            raise InvalidURL("Invalid hostname")
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise InvalidURL("Invalid port")
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        netloc = f"[{host}]" if ":" in host else host
        if port and not default_port:
            netloc += f":{port}"
        path = parts.path or "/"
        while "//" in path:
            path = path.replace("//", "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((scheme, netloc, path, parts.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise InvalidURL(str(exc)) from exc


def hostname(url: str) -> str:
    return urlsplit(url).hostname or ""


def is_private_address(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".localhost"):
        return True
    try:
        return not ipaddress.ip_address(lowered).is_global
    except ValueError:
        return False
