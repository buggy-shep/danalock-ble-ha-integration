# 0026 — Optional HTTP proxy for Danalock cloud access

- **Status:** approved (owner request recorded in the group conversation,
  2026-10-31: add an HTTP proxy setting with address, port, username, and
  password)
- **Scope:** `custom_components/danalock_ble/config_flow.py`,
  `custom_components/danalock_ble/__init__.py`,
  `custom_components/danalock_ble/const.py`,
  `custom_components/danalock_ble/proxy.py` (new),
  `custom_components/danalock_ble/translations/{en,ru}.json`,
  `README.md`, and the `manifest.json` version.

## Summary

Allow the integration to reach the Danalock cloud through an optional HTTP
proxy. The proxy is configured with an address, port, username, and password;
blank credentials mean an unauthenticated proxy, and a blank address disables
the proxy. The setting is available on the first login form (so an account can
be set up on a network where direct access is blocked) and in the options
dialog (so it can be changed later).

## Motivation

The integration is unusable on networks where the Danalock cloud API is only
reachable through an HTTP proxy. The cloud library already accepts an injected
`httpx` transport, so the integration can supply a proxy transport without
changing the library API or adding a new PyPI dependency resolution path.

## Requirements

- R1 (MUST) Add the optional settings `proxy_host`, `proxy_port`,
  `proxy_username`, and `proxy_password`.
- R2 (MUST) A blank `proxy_host` disables the proxy and removes all proxy
  keys (including the password) from the entry options on save.
- R3 (MUST) With a non-empty host, the proxy URL is
  `http://<host>:<port>`; a bracketed or bare IPv6 address is accepted. Port
  defaults to 8080 and must be in 1–65535. A scheme, path, credentials, or
  whitespace in the host field is rejected with the `invalid_proxy` flow
  error.
- R4 (MUST) `proxy_username` is optional. When set, the client sends HTTP
  Basic proxy authentication with `proxy_password`; a password without a
  username is rejected with `invalid_proxy`.
- R5 (MUST) All cloud requests made by the entry use the proxy: the OAuth2
  token requests, device list, key retrieval, and firmware lookup, because
  both clients of `AsyncDanalockCloud` share the injected transport.
- R6 (MUST) The initial `user` step accepts the four optional proxy fields and
  validates the cloud login through them. The created entry stores the
  normalized proxy settings in `options`, not in `data`.
- R7 (MUST) The options dialog keeps the existing key-refresh settings and
  adds the proxy fields, prefilled from the current options. A changed setting
  reloads the entry.
- R8 (MUST) Reauth and reconfigure validation use the proxy from the entry
  options; they do not change or clear it.
- R9 (MUST) The cloud account password remains transient and is never stored.
  The proxy password is persisted in Home Assistant's internal entry storage,
  like other connection settings, and must never be written to logs.
- R10 (MUST) The `invalid_proxy` error is translated in English and Russian;
  both translation files keep identical key trees.
- R11 (MUST) Bump `manifest.json` `version` to `0.10.0`; no library pin
  changes (the transport injection point exists in `pydanalock-cloud==0.5.1`).
- R12 (MUST) The local gate (`pytest -m "not live"`) is green; `hassfest` and
  `hacs/action` stay green with no ignores.

## Design

### Settings and normalization

Constants live in `const.py`:

```python
CONF_PROXY_HOST = "proxy_host"
CONF_PROXY_PORT = "proxy_port"
CONF_PROXY_USERNAME = "proxy_username"
CONF_PROXY_PASSWORD = "proxy_password"
DEFAULT_PROXY_PORT = 8080
```

A new `proxy.py` owns the parsing rules:

- `normalized_proxy_options(values) -> dict[str, Any]` trims the host and
  username, validates the host and port, and returns either `{}` (blank host)
  or the four normalized keys. A password without a username raises
  `ProxyConfigError`.
- `proxy_from_options(options) -> httpx.Proxy | None` builds
  `http://host:port` (IPv6 in brackets) with `auth=(username, password)` when
  a username is present.

Accepted hosts are hostnames, IPv4 addresses, and IPv6 addresses (with or
without brackets). A `://` scheme, `/`, `@`, `?`, `#`, or whitespace is
rejected before `httpx.Proxy` is created.

### Transport injection

`new_cloud_client` gains a third positional argument `proxy`:

```python
async def new_cloud_client(
    hass: HomeAssistant,
    storage: TokenStorage,
    proxy: httpx.Proxy | None = None,
) -> AsyncDanalockCloud:
    def _build() -> AsyncDanalockCloud:
        if proxy is None:
            return AsyncDanalockCloud(storage=storage)
        return AsyncDanalockCloud(
            storage=storage,
            transport=httpx.AsyncHTTPTransport(proxy=proxy),
        )

    return await hass.async_add_executor_job(_build)
```

When no proxy is configured the transport keyword is omitted, so the existing
test seam (`monkeypatch` of `AsyncDanalockCloud` + `MockTransport`) and HA's
own HTTP behavior are unchanged.

`async_setup_entry` reads `entry.options` through `proxy_from_options`; an
invalid stored configuration raises `ConfigEntryError` instead of silently
falling back to a direct connection. The config-flow options supply a validated
value, so this path is only reachable with manually corrupted storage.

### Config flow

The `user` schema gets four optional fields:

| Field | Selector | Default |
|---|---|---|
| `proxy_host` | text | empty |
| `proxy_port` | coerced int, 1–65535 | 8080 |
| `proxy_username` | text | empty |
| `proxy_password` | password text | empty |

Validation order: normalize the proxy settings (typed `invalid_proxy` error),
then run the cloud login and key retrieval. On success the entry is created
with `options=normalized_proxy_options(user_input)`.

The options schema keeps the refresh fields and adds the same four fields with
defaults from `config_entry.options`. On submit it stores the refresh values
plus the normalized proxy settings; a blank host drops the proxy keys.

Reauth and reconfigure read the proxy from `entry.options` and reuse the
existing username/password forms; they do not expose or clear the proxy.

## API

No public library API changes. The integration uses the existing
`transport` parameter of `AsyncDanalockCloud`.

## Test plan

- TDD: `tests/test_proxy.py` covers normalization (blank host, trimming,
  defaults, IPv6 brackets, scheme rejection, password-without-username) and
  proxy construction (URL and Basic auth).
- `tests/test_proxy.py` also drives the config flow: the user form accepts a
  proxy and the real client is built with an `httpx.AsyncHTTPTransport`
  carrying that proxy (captured by a transport factory that still answers
  through the mock cloud handler); an invalid host shows `invalid_proxy` and
  creates no entry; the options dialog stores and clears proxy settings;
  reauth/reconfigure use the stored proxy.
- `tests/test_translations.py` already enforces key-tree parity, so the new
  keys are added to both languages in one change.
- Regression: the full `pytest -m "not live"` suite stays green, including
  all existing flows with no proxy.

## Acceptance criteria

- A user can set an HTTP proxy (address, port, optional username/password)
  during first setup or later in the options dialog.
- Cloud requests go through the proxy when configured; no proxy is used when
  the address is blank.
- Credentials are masked in the UI, persisted only as entry options, and
  never logged.
- Existing tests and HA/HACS validation remain green.
- Manifest version is `0.10.0`.

## Out of scope

- HTTPS proxy (CONNECT) configuration, SOCKS proxies, PAC files, and proxy
  auto-discovery.
- Per-endpoint proxy bypass lists.
- A separate proxy for the firmware service; it shares the cloud transport.
- Changes to `pydanalock-cloud`; proxy support is supplied by the host through
  the existing transport injection point.
