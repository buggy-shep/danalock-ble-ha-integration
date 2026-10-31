"""HTTP proxy settings for the Danalock cloud (spec 0026).

The proxy is stored in the config entry options. A blank host disables it;
a host is turned into an ``http://`` proxy URL with an optional Basic auth
pair for the injected httpx transport.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from typing import Any

import httpx

from custom_components.danalock_ble.const import (
    CONF_PROXY_HOST,
    CONF_PROXY_PASSWORD,
    CONF_PROXY_PORT,
    CONF_PROXY_USERNAME,
    DEFAULT_PROXY_PORT,
)

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


class ProxyConfigError(ValueError):
    """The proxy settings cannot be turned into a usable proxy."""


def _normalized_host(raw: str) -> str:
    """Validate a host and return it without IPv6 brackets."""
    host = raw.strip()
    if (
        "://" in host
        or any(char in host for char in "/@?#")
        or any(char.isspace() for char in host)
    ):
        raise ProxyConfigError("the proxy address must be a host name or IP address")
    if host.startswith("[") or host.endswith("]"):
        if not (host.startswith("[") and host.endswith("]")):
            raise ProxyConfigError("the proxy IPv6 address has mismatched brackets")
        host = host[1:-1]
    if not host:
        raise ProxyConfigError("the proxy address is empty")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME_RE.match(host):
            raise ProxyConfigError(
                "the proxy address must be a host name or IP address"
            ) from None
    return host


def normalized_proxy_options(values: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the four proxy keys (spec 0026 R1–R4).

    A blank host returns ``{}`` so disabling the proxy also drops the stored
    credentials. Everything else is returned trimmed, with an integer port.
    """
    host = str(values.get(CONF_PROXY_HOST) or "").strip()
    if not host:
        return {}
    host = _normalized_host(host)
    try:
        port = int(values.get(CONF_PROXY_PORT, DEFAULT_PROXY_PORT))
    except (TypeError, ValueError) as err:
        raise ProxyConfigError("the proxy port must be a number") from err
    if not 1 <= port <= 65535:
        raise ProxyConfigError("the proxy port must be between 1 and 65535")
    username = str(values.get(CONF_PROXY_USERNAME) or "").strip()
    password = str(values.get(CONF_PROXY_PASSWORD) or "")
    if password and not username:
        raise ProxyConfigError("a proxy password requires a proxy username")
    return {
        CONF_PROXY_HOST: host,
        CONF_PROXY_PORT: port,
        CONF_PROXY_USERNAME: username,
        CONF_PROXY_PASSWORD: password,
    }


def _proxy_url(host: str, port: int) -> str:
    """Build the http proxy URL, bracketing IPv6 addresses."""
    if ":" in host:
        return f"http://[{host}]:{port}"
    return f"http://{host}:{port}"


def proxy_from_options(options: Mapping[str, Any]) -> httpx.Proxy | None:
    """Build the httpx proxy for an entry's options (spec 0026 R2–R4)."""
    normalized = normalized_proxy_options(options)
    if not normalized:
        return None
    username = normalized[CONF_PROXY_USERNAME]
    auth = (username, normalized[CONF_PROXY_PASSWORD]) if username else None
    try:
        return httpx.Proxy(
            _proxy_url(normalized[CONF_PROXY_HOST], normalized[CONF_PROXY_PORT]),
            auth=auth,
        )
    except (httpx.InvalidURL, ValueError) as err:
        raise ProxyConfigError("the proxy settings are not a valid URL") from err
