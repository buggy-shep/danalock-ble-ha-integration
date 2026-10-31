"""HTTP proxy settings for cloud access (spec 0026)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.danalock_ble.const import (
    CONF_PROXY_HOST,
    CONF_PROXY_PASSWORD,
    CONF_PROXY_PORT,
    CONF_PROXY_USERNAME,
    DEFAULT_PROXY_PORT,
    DOMAIN,
)
from custom_components.danalock_ble.proxy import (
    ProxyConfigError,
    normalized_proxy_options,
    proxy_from_options,
)
from tests.conftest import (
    PASSWORD,
    USERNAME,
    CloudHandler,
    patch_cloud_transport,
    setup_entry,
)
from tests.test_config_flow import submit, submit_options


def proxy_values(**overrides: Any) -> dict[str, Any]:
    """A complete proxy form submission."""
    values: dict[str, Any] = {
        CONF_PROXY_HOST: "proxy.example.com",
        CONF_PROXY_PORT: 3128,
        CONF_PROXY_USERNAME: "proxy-user",
        CONF_PROXY_PASSWORD: "proxy-secret",
    }
    values.update(overrides)
    return values


class TransportRecorder:
    """Record the proxy handed to httpx and answer through the cloud mock."""

    def __init__(self, handler: CloudHandler) -> None:
        self.handler = handler
        self.proxies: list[httpx.Proxy | None] = []

    def __call__(self, **kwargs: Any) -> httpx.MockTransport:
        self.proxies.append(kwargs.get("proxy"))
        return httpx.MockTransport(self.handler)


# --- normalization and URL construction --------------------------------------


def test_blank_host_disables_proxy() -> None:
    """An empty or whitespace-only host means "no proxy" (spec 0026 R2)."""
    assert normalized_proxy_options({}) == {}
    assert normalized_proxy_options({CONF_PROXY_HOST: "   "}) == {}
    assert proxy_from_options({CONF_PROXY_HOST: ""}) is None


def test_proxy_options_are_normalized() -> None:
    """Host and username are trimmed, the port is coerced (spec 0026 R1/R3)."""
    normalized = normalized_proxy_options(
        {
            CONF_PROXY_HOST: "  proxy.example.com  ",
            CONF_PROXY_PORT: "3128",
            CONF_PROXY_USERNAME: "  proxy-user ",
            CONF_PROXY_PASSWORD: " proxy-secret ",
        }
    )
    assert normalized == {
        CONF_PROXY_HOST: "proxy.example.com",
        CONF_PROXY_PORT: 3128,
        CONF_PROXY_USERNAME: "proxy-user",
        CONF_PROXY_PASSWORD: " proxy-secret ",
    }


def test_proxy_default_port() -> None:
    """The port defaults to 8080 when the form omits it (spec 0026 R3)."""
    normalized = normalized_proxy_options({CONF_PROXY_HOST: "proxy.example.com"})
    assert normalized[CONF_PROXY_PORT] == DEFAULT_PROXY_PORT


def test_proxy_with_basic_auth() -> None:
    """A username produces HTTP Basic auth with the proxy password."""
    proxy = proxy_from_options(proxy_values())
    assert proxy is not None
    assert str(proxy.url) == "http://proxy.example.com:3128"
    assert proxy.auth == ("proxy-user", "proxy-secret")


def test_proxy_without_credentials() -> None:
    """Blank credentials configure an unauthenticated proxy (spec 0026 R4)."""
    proxy = proxy_from_options(
        proxy_values(**{CONF_PROXY_USERNAME: "", CONF_PROXY_PASSWORD: ""})
    )
    assert proxy is not None
    assert proxy.auth is None


def test_ipv6_proxy_url_is_bracketed() -> None:
    """A bare IPv6 address is rendered in brackets (spec 0026 R3)."""
    proxy = proxy_from_options(
        proxy_values(**{CONF_PROXY_HOST: "2001:db8::1", CONF_PROXY_PORT: 8080})
    )
    assert proxy is not None
    assert str(proxy.url) == "http://[2001:db8::1]:8080"


def test_bracketed_ipv6_proxy_host_is_accepted() -> None:
    """Users may paste an already bracketed IPv6 address."""
    proxy = proxy_from_options(proxy_values(**{CONF_PROXY_HOST: "[::1]"}))
    assert proxy is not None
    assert str(proxy.url) == "http://[::1]:3128"


@pytest.mark.parametrize(
    "host",
    [
        "http://proxy.example.com",
        "proxy.example.com/path",
        "bad host",
        "-proxy.example.com",
        "proxy.example.com:3128",
    ],
)
def test_invalid_proxy_host_is_rejected(host: str) -> None:
    """A scheme, path, whitespace, or punctuation in the host is invalid."""
    with pytest.raises(ProxyConfigError):
        proxy_from_options(proxy_values(**{CONF_PROXY_HOST: host}))


@pytest.mark.parametrize("host", ["[::1", "::1]", "[]"])
def test_malformed_brackets_are_rejected(host: str) -> None:
    """An IPv6 address must use a matching bracket pair (spec 0026 R3)."""
    with pytest.raises(ProxyConfigError):
        proxy_from_options(proxy_values(**{CONF_PROXY_HOST: host}))


@pytest.mark.parametrize("port", ["not-a-port", None])
def test_non_numeric_port_is_rejected(port: Any) -> None:
    """The port must be convertible to an integer (spec 0026 R3)."""
    with pytest.raises(ProxyConfigError):
        proxy_from_options(proxy_values(**{CONF_PROXY_PORT: port}))


def test_invalid_proxy_url_from_httpx_is_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An httpx URL rejection surfaces as ProxyConfigError, not a crash."""

    def reject(*args: Any, **kwargs: Any) -> httpx.Proxy:
        raise httpx.InvalidURL("bad proxy URL")

    monkeypatch.setattr(httpx, "Proxy", reject)
    with pytest.raises(ProxyConfigError):
        proxy_from_options(proxy_values())


def test_password_without_username_is_rejected() -> None:
    """A proxy password needs a username (spec 0026 R4)."""
    with pytest.raises(ProxyConfigError):
        proxy_from_options(
            proxy_values(**{CONF_PROXY_USERNAME: "", CONF_PROXY_PASSWORD: "secret"})
        )


@pytest.mark.parametrize("port", [0, 65536])
def test_out_of_range_port_is_rejected(port: int) -> None:
    """The port must be a valid TCP port (spec 0026 R3)."""
    with pytest.raises(ProxyConfigError):
        proxy_from_options(proxy_values(**{CONF_PROXY_PORT: port}))


# --- config flow -------------------------------------------------------------


async def test_user_flow_accepts_proxy(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First setup can validate the cloud through a proxy (spec 0026 R6)."""
    handler = CloudHandler()
    recorder = TransportRecorder(handler)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", recorder)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await submit(
        hass,
        result,
        {"username": USERNAME, "password": PASSWORD, **proxy_values()},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options == proxy_values()
    assert recorder.proxies
    assert str(recorder.proxies[0].url) == "http://proxy.example.com:3128"
    assert recorder.proxies[0].auth == ("proxy-user", "proxy-secret")


async def test_user_flow_invalid_proxy_shows_error(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable proxy address is a typed form error (spec 0026 R3/R10)."""
    handler = CloudHandler()
    recorder = TransportRecorder(handler)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", recorder)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await submit(
        hass,
        result,
        {
            "username": USERNAME,
            "password": PASSWORD,
            **proxy_values(**{CONF_PROXY_HOST: "http://proxy.example.com"}),
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_proxy"}
    assert hass.config_entries.async_entries(DOMAIN) == []
    assert recorder.proxies == []


async def test_options_flow_saves_proxy_and_reloads(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The options dialog stores the proxy and reloads the entry (R7)."""
    handler = CloudHandler()
    recorder = TransportRecorder(handler)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", recorder)
    entry = await setup_entry(hass, monkeypatch, handler)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await submit_options(
        hass,
        result,
        {
            "refresh_period_hours": 24,
            "refresh_jitter_hours": 3,
            **proxy_values(),
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "refresh_period_hours": 24,
        "refresh_jitter_hours": 3,
        **proxy_values(),
    }
    assert recorder.proxies
    assert str(recorder.proxies[-1].url) == "http://proxy.example.com:3128"


async def test_options_flow_clears_proxy_when_host_is_blank(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank host removes the proxy settings, including the password (R2)."""
    handler = CloudHandler()
    recorder = TransportRecorder(handler)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", recorder)
    entry = await setup_entry(hass, monkeypatch, handler, options=proxy_values())

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await submit_options(
        hass,
        result,
        {
            "refresh_period_hours": 12,
            "refresh_jitter_hours": 6,
            CONF_PROXY_HOST: "",
            CONF_PROXY_PORT: DEFAULT_PROXY_PORT,
            CONF_PROXY_USERNAME: "proxy-user",
            CONF_PROXY_PASSWORD: "proxy-secret",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "refresh_period_hours": 12,
        "refresh_jitter_hours": 6,
    }
    assert proxy_from_options(entry.options) is None


async def test_options_flow_invalid_proxy_shows_error(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The options dialog reports an invalid proxy and saves nothing (R3)."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await submit_options(
        hass,
        result,
        {
            "refresh_period_hours": 12,
            "refresh_jitter_hours": 6,
            **proxy_values(**{CONF_PROXY_USERNAME: "", CONF_PROXY_PASSWORD: "secret"}),
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_proxy"}
    assert entry.options == {}


async def test_options_form_prefills_proxy(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saved proxy values are shown again in the options form (spec 0026 R7)."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler, options=proxy_values())

    result = await hass.config_entries.options.async_init(entry.entry_id)

    defaults = result["data_schema"]({})
    assert defaults[CONF_PROXY_HOST] == "proxy.example.com"
    assert defaults[CONF_PROXY_PORT] == 3128
    assert defaults[CONF_PROXY_USERNAME] == "proxy-user"
    assert defaults[CONF_PROXY_PASSWORD] == "proxy-secret"


async def test_reauth_uses_stored_proxy(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revalidation after a reauth goes through the stored proxy (R8)."""
    handler = CloudHandler()
    recorder = TransportRecorder(handler)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", recorder)
    entry = await setup_entry(hass, monkeypatch, handler, options=proxy_values())
    assert recorder.proxies

    result = await entry.start_reauth_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert recorder.proxies[-1] is not None
    assert str(recorder.proxies[-1].url) == "http://proxy.example.com:3128"


async def test_reconfigure_uses_stored_proxy(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconfigure validation also goes through the stored proxy (R8)."""
    handler = CloudHandler()
    recorder = TransportRecorder(handler)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", recorder)
    entry = await setup_entry(hass, monkeypatch, handler, options=proxy_values())

    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert recorder.proxies[-1] is not None
    assert str(recorder.proxies[-1].url) == "http://proxy.example.com:3128"


async def test_no_proxy_transport_is_created_without_proxy(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a proxy the integration keeps the default transport (R5)."""
    handler = CloudHandler()
    recorder = TransportRecorder(handler)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", recorder)
    patch_cloud_transport(monkeypatch, handler)

    await setup_entry(hass, monkeypatch, handler)

    assert recorder.proxies == []
