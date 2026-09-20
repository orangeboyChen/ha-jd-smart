"""Tests for JD Smart API client connectivity failures."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import Mock

import pytest
from aiohttp import ClientConnectorError
from aiohttp.client_reqrep import ConnectionKey
from multidict import CIMultiDict

from custom_components.jd_smart.api import (
    REQUEST_TIMEOUT,
    JdSmartCannotConnectError,
    JdSmartClient,
    JdSmartCredentials,
    JdSmartDeviceProfile,
    JdSmartTokenRefreshError,
)


class _RequestContext:
    """Async context manager standing in for `session.post()`."""

    def __init__(
        self, response: _Response | None = None, error: Exception | None = None
    ) -> None:
        """Store the response or the error to raise."""
        self._response = response
        self._error = error

    async def __aenter__(self) -> Any:
        """Raise or return the canned response."""
        if self._error is not None:
            raise self._error
        return self._response

    async def __aexit__(self, *_args: object) -> bool:
        """Exit without suppressing exceptions."""
        return False


class _Response:
    """Minimal `aiohttp.ClientResponse` stand-in."""

    def __init__(self, status: HTTPStatus, text: str) -> None:
        """Store the canned status and body."""
        self.status = status
        self._text = text
        self.request_info = Mock()
        self.history: tuple[Any, ...] = ()
        self.headers = CIMultiDict()

    async def text(self) -> str:
        """Return the canned body."""
        return self._text


class _Session:
    """Record requests and replay a canned response or error."""

    def __init__(
        self, response: _Response | None = None, error: Exception | None = None
    ) -> None:
        """Store the response or the error to replay."""
        self._context = _RequestContext(response, error)
        self.calls: list[dict[str, Any]] = []

    def post(self, _url: str, **kwargs: Any) -> _RequestContext:
        """Record one request and return the canned context manager."""
        self.calls.append(kwargs)
        return self._context


def _connector_error(message: str) -> ClientConnectorError:
    """Return the error aiohttp raises when a host cannot be resolved."""
    return ClientConnectorError(
        ConnectionKey("api.smart.jd.com", 443, True, True, None, None, None),
        OSError(None, message),
    )


def _client(session: _Session) -> JdSmartClient:
    """Return a client wired to the given session."""
    return JdSmartClient(
        session,  # type: ignore[arg-type]
        JdSmartCredentials(cookie="cookie", tgt="tgt"),
        JdSmartDeviceProfile(device_id="device-id"),
    )


async def test_snapshot_reports_dns_failure_reason() -> None:
    """A DNS failure is surfaced with the underlying resolver message."""
    session = _Session(error=_connector_error("dns cannot resolve"))

    with pytest.raises(JdSmartCannotConnectError, match="dns cannot resolve"):
        await _client(session).async_get_snapshot("feed-id")


async def test_snapshot_reports_http_status() -> None:
    """An HTTP failure is surfaced with its status code."""
    session = _Session(response=_Response(HTTPStatus.BAD_GATEWAY, "bad gateway"))

    with pytest.raises(JdSmartCannotConnectError, match="502"):
        await _client(session).async_get_snapshot("feed-id")


async def test_snapshot_request_has_an_explicit_timeout() -> None:
    """Requests never inherit the five minute aiohttp default timeout."""
    session = _Session(error=_connector_error("dns cannot resolve"))

    with pytest.raises(JdSmartCannotConnectError):
        await _client(session).async_get_snapshot("feed-id")

    assert session.calls[0]["timeout"] is REQUEST_TIMEOUT


async def test_wangyin_handshake_reports_failure_reason() -> None:
    """A failed Wangyin handshake is surfaced with the underlying reason."""
    session = _Session(error=_connector_error("dns cannot resolve"))

    with pytest.raises(
        JdSmartCannotConnectError, match="Wangyin handshake.*dns cannot resolve"
    ):
        await _client(session).async_get_devices()

    assert session.calls[0]["timeout"] is REQUEST_TIMEOUT


async def test_token_refresh_reports_failure_reason() -> None:
    """A failed token refresh is surfaced with the underlying reason."""
    session = _Session(error=_connector_error("dns cannot resolve"))

    with pytest.raises(JdSmartTokenRefreshError, match="dns cannot resolve"):
        await _client(session).async_refresh_token()

    assert session.calls[0]["timeout"] is REQUEST_TIMEOUT
