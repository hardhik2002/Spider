import asyncio
import ipaddress
import socket

from app.crawler.normalizer import hostname, is_private_address, normalize_url


class UnsafeTarget(ValueError):
    pass


class TargetValidator:
    async def validate(self, url: str) -> str:
        normalized = normalize_url(url)
        host = hostname(normalized)
        if is_private_address(host):
            raise UnsafeTarget("Private or local target is blocked")
        try:
            addresses = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise UnsafeTarget(f"DNS resolution failed: {exc}") from exc
        if not addresses or any(
            not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses
        ):
            raise UnsafeTarget("DNS resolved to a non-public address")
        return normalized
