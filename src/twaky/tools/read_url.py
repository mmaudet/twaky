"""HTML → text @tool via httpx + trafilatura."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from langchain_core.tools import tool


def _validate_url(url: str) -> None:
    """
    Validate URL against SSRF attacks.

    Rejects non-HTTP schemes, missing hostnames, and private/reserved IPs.
    """
    parsed = urlparse(url)

    # Require http/https scheme
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"disallowed scheme: {parsed.scheme}")

    # Require hostname
    if not parsed.hostname:
        raise ValueError("missing hostname")

    # Resolve hostname and reject private/reserved IPs
    try:
        addrs = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"hostname resolution failed: {parsed.hostname}") from e

    for addr_info in addrs:
        # addr_info = (family, type, proto, canonname, sockaddr)
        # sockaddr = (host, port) for IPv4/IPv6
        addr_str = addr_info[4][0]
        ip = ipaddress.ip_address(addr_str)

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"blocked host: {parsed.hostname} -> {ip}")


async def _fetch_and_extract(url: str, max_chars: int) -> str:
    _validate_url(url)

    async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as client:
        current_url = url
        max_redirects = 5
        redirect_count = 0

        while True:
            resp = await client.get(current_url)
            resp.raise_for_status()

            # Handle redirects manually
            if resp.status_code in {301, 302, 303, 307, 308}:
                if redirect_count >= max_redirects:
                    raise ValueError(f"Too many redirects (max {max_redirects})")

                location = resp.headers.get("location")
                if not location:
                    break

                # Resolve relative redirect against current URL
                current_url = urljoin(current_url, location)
                _validate_url(current_url)
                redirect_count += 1
            else:
                break

    text = trafilatura.extract(resp.text) or ""
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


@tool
def read_url(url: str, max_chars: int = 8000) -> str:
    """Fetch a web page and return its main text content (up to max_chars)."""
    return asyncio.run(_fetch_and_extract(url, max_chars))


__all__ = ["read_url"]
