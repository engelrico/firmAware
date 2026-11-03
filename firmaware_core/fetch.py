"""Fetching sources over HTTP(S) with URL, redirect, size, and charset checks."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .constants import ACCEPT_HEADER, BINARY_MEDIA_TYPES, BINARY_SUBTYPES, MAX_RESPONSE_BYTES, REQUEST_TIMEOUT_SECONDS, USER_AGENT
from .errors import FirmwareSourceError


@dataclass(frozen=True)
class FetchedDocument:
    """Result of a source fetch along with metadata for preview and export."""

    text: str
    status: int | None
    final_url: str
    charset: str
    requested_url: str
    requested_encoding: str | None


def validate_url_basic(url: Any) -> None:
    """Validate protocol, host, control characters, and missing URL credentials."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Required field 'source_url' is missing or invalid")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ValueError("URL contains control characters")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid source URL '{url}'")
    if parsed.username or parsed.password:
        raise ValueError("URL must not contain credentials")


class _RedirectValidationHandler(HTTPRedirectHandler):
    """Validate each redirect target again for protocol, host, and credentials."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        try:
            validate_url_basic(newurl)
        except ValueError as error:
            raise ValueError(f"Invalid redirect target: {error}") from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _reject_binary_content_type(content_type: str) -> None:
    """Reject obviously binary responses unless a charset was chosen."""
    main_type, _, sub_type = content_type.partition("/")
    if main_type in BINARY_MEDIA_TYPES or sub_type in BINARY_SUBTYPES:
        raise FirmwareSourceError(f"Unreadable content type: {content_type}")


def fetch_document(url: str, encoding: str | None = None) -> FetchedDocument:
    """Fetch a source and return text, status, final URL, and charset."""
    opener = build_opener(_RedirectValidationHandler())
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT_HEADER})
    with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        status = getattr(response, "status", None) or response.getcode()
        final_url = response.geturl()
        content_type = response.headers.get_content_type()
        data = response.read(MAX_RESPONSE_BYTES + 1)
        charset = encoding or response.headers.get_content_charset() or "utf-8"

    if len(data) > MAX_RESPONSE_BYTES:
        raise FirmwareSourceError("Response exceeds the size limit of 5 MiB")
    if encoding is None:
        _reject_binary_content_type(content_type)

    try:
        text = data.decode(charset)
    except LookupError:
        raise FirmwareSourceError(f"Unknown charset '{charset}'") from None
    except UnicodeDecodeError as error:
        raise FirmwareSourceError(
            f"Decoding with '{charset}' failed: {error}"
        ) from None

    return FetchedDocument(
        text=text,
        status=status,
        final_url=final_url,
        charset=charset,
        requested_url=url,
        requested_encoding=encoding,
    )


def fetch_source(device: Mapping[str, Any]) -> str:
    """Fetch the source and decode the response based on its charset."""
    return fetch_document(device["source_url"], device.get("encoding")).text
