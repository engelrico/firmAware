"""HTTP server for the web interface.

Standard library only. The server binds to the loopback interface by default. On any
other interface a token is required, because the interface can edit the entry list and
trigger network requests.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import re
import socket
import sys
import traceback
from collections.abc import Callable
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from ..service import NotFoundError, ServiceError, Workspace
from ..state import read_json
from .jobs import CheckJobs

STATIC_DIR = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


def static_files() -> dict[str, tuple[str, str]]:
    """The files of the flat static directory by URL path; nothing else is ever served."""
    files = {}
    for path in sorted(STATIC_DIR.iterdir()):
        content_type = CONTENT_TYPES.get(path.suffix)
        if path.is_file() and content_type:
            files["/" if path.name == "index.html" else f"/{path.name}"] = (path.name, content_type)
    return files


STATIC_FILES = static_files()
COOKIE_NAME = "firmaware_token"
REQUEST_HEADER = "X-Requested-With"
REQUEST_HEADER_VALUE = "firmaware"
MAX_BODY_BYTES = 64 * 1024
MIN_TOKEN_LENGTH = 16
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
ENTRY_ID = r"([^/]+)"


def is_loopback(host: str) -> bool:
    """True for ``localhost`` and loopback addresses."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_web_token(path: Path) -> str | None:
    """Read the access token from the web config; None if the file does not exist."""
    if not path.exists():
        return None
    data = read_json(path)
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(
            f"{path.name}: 'token' must be a string of at least {MIN_TOKEN_LENGTH} characters"
        )
    return token


class WebServer(ThreadingHTTPServer):
    """Threaded server that carries the workspace and the access settings."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        workspace: Workspace,
        token: str | None = None,
    ) -> None:
        host = address[0]
        if not is_loopback(host) and not token:
            raise ValueError("A token is required when the server is reachable from the network")
        self.workspace = workspace
        self.jobs = CheckJobs(workspace)
        self.token = token
        self.loopback = is_loopback(host)
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        super().__init__(address, Handler)
        port = self.server_address[1]
        self.allowed_hosts = {f"{name}:{port}" for name in ("localhost", "127.0.0.1", "[::1]")}


class Handler(BaseHTTPRequestHandler):
    """Routes requests to the service layer; every response is JSON or a static file."""

    protocol_version = "HTTP/1.1"
    server: WebServer

    # --- plumbing ------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """No access log; errors are reported explicitly."""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in {**SECURITY_HEADERS, **(extra_headers or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # --- access control --------------------------------------------------

    def _host_allowed(self) -> bool:
        """Without a token only loopback names are accepted (DNS rebinding protection)."""
        if self.server.token:
            return True
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def _token_from_request(self) -> str:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[7:].strip()
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:  # malformed cookie header
            return ""
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel else ""

    def _authorized(self) -> bool:
        token = self.server.token
        if not token:
            return True
        candidate = self._token_from_request()
        return bool(candidate) and hmac.compare_digest(candidate.encode(), token.encode())

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return urlsplit(origin).netloc == self.headers.get("Host", "")

    def _read_json_body(self) -> Any:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return {}
        try:
            length = int(length_header)
        except ValueError:
            raise ServiceError("Invalid Content-Length") from None
        if length < 0:
            raise ServiceError("Invalid Content-Length")
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise _PayloadTooLarge()
        raw = self.rfile.read(length)
        if not raw:
            return {}
        if self.headers.get_content_type() != "application/json":
            raise ServiceError("Content-Type must be application/json")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ServiceError("Request body is not valid JSON") from None

    # --- dispatch --------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        try:
            if not self._host_allowed():
                self.close_connection = True
                return self._error(HTTPStatus.FORBIDDEN, "Host not allowed")

            if method == "GET" and path == "/" and self.server.token:
                supplied = parse_qs(parts.query).get("token", [""])[0]
                if supplied and hmac.compare_digest(supplied.encode(), self.server.token.encode()):
                    return self._login_redirect()

            if not self._authorized():
                if path.startswith("/api/"):
                    return self._error(HTTPStatus.UNAUTHORIZED, "Token required")
                return self._send(
                    HTTPStatus.UNAUTHORIZED,
                    b"Token required. Open this address once with ?token=<your token>.",
                    "text/plain; charset=utf-8",
                )

            if path.startswith("/api/"):
                return self._api(method, path)
            if method != "GET":
                return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")
            return self._static(path)
        except _PayloadTooLarge:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        except ServiceError as error:
            self._error(error.status, str(error))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal error")

    def _login_redirect(self) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={self.server.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000",
        )
        self.send_header("Content-Length", "0")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()

    def _static(self, path: str) -> None:
        entry = STATIC_FILES.get(path)
        if entry is None:
            return self._error(HTTPStatus.NOT_FOUND, "Not found")
        name, content_type = entry
        try:
            body = (STATIC_DIR / name).read_bytes()
        except OSError:
            return self._error(HTTPStatus.NOT_FOUND, "Not found")
        self._send(HTTPStatus.OK, body, content_type)

    # --- API -------------------------------------------------------------

    def _api(self, method: str, path: str) -> None:
        if method != "GET":
            # Mutating requests must come from the page itself: a custom header cannot
            # be sent cross-origin without a CORS preflight, which this server never allows.
            if self.headers.get(REQUEST_HEADER) != REQUEST_HEADER_VALUE or not self._same_origin():
                return self._error(HTTPStatus.FORBIDDEN, "Cross-site request rejected")
        for route_method, pattern, handler in self._routes():
            match = pattern.fullmatch(path)
            if match and route_method == method:
                args = [unquote(group) for group in match.groups()]
                return handler(*args)
        known_path = any(pattern.fullmatch(path) for _, pattern, _ in self._routes())
        status = HTTPStatus.METHOD_NOT_ALLOWED if known_path else HTTPStatus.NOT_FOUND
        self._error(status, "Method not allowed" if known_path else "Not found")

    def _body(self) -> dict[str, Any]:
        """The JSON request body, which must be an object."""
        data = self._read_json_body()
        if not isinstance(data, dict):
            raise ServiceError("Request body must be a JSON object")
        return data

    def _routes(self) -> list[tuple[str, re.Pattern[str], Callable[..., None]]]:
        workspace = self.server.workspace
        jobs = self.server.jobs
        ok = HTTPStatus.OK

        def entries() -> None:
            self._json(ok, {"entries": workspace.entries()})

        def entry(entry_id: str) -> None:
            self._json(ok, workspace.entry(entry_id))

        def profiles() -> None:
            self._json(ok, {"profiles": workspace.profiles()})

        def update(entry_id: str) -> None:
            workspace.update_entry(entry_id, self._body())
            self._json(ok, workspace.entry(entry_id))

        def installed_latest(entry_id: str) -> None:
            workspace.mark_installed_latest(entry_id)
            self._json(ok, workspace.entry(entry_id))

        def add() -> None:
            body = self._body()
            workspace.add_entry(body.get("profile"), body.get("id"), body.get("name"))
            self._json(HTTPStatus.CREATED, workspace.entry(body["id"]))

        def duplicate(entry_id: str) -> None:
            new_id = self._body().get("id")
            workspace.duplicate_entry(entry_id, new_id)
            self._json(HTTPStatus.CREATED, workspace.entry(new_id))

        def delete(entry_id: str) -> None:
            workspace.delete_entry(entry_id)
            self._json(ok, {"ok": True})

        def order() -> None:
            workspace.reorder(self._body().get("ids"))
            self._json(ok, {"entries": workspace.entries()})

        def start_check() -> None:
            self._json(HTTPStatus.ACCEPTED, {"job": jobs.start(self._body().get("ids"))})

        def latest_check() -> None:
            self._json(ok, {"job": jobs.latest()})

        def check(job_id: str) -> None:
            self._json(ok, {"job": jobs.get(job_id)})

        return [
            ("GET", re.compile(r"/api/entries"), entries),
            ("POST", re.compile(r"/api/entries"), add),
            ("PUT", re.compile(r"/api/order"), order),
            ("GET", re.compile(rf"/api/entries/{ENTRY_ID}"), entry),
            ("PATCH", re.compile(rf"/api/entries/{ENTRY_ID}"), update),
            ("DELETE", re.compile(rf"/api/entries/{ENTRY_ID}"), delete),
            ("POST", re.compile(rf"/api/entries/{ENTRY_ID}/installed-latest"), installed_latest),
            ("POST", re.compile(rf"/api/entries/{ENTRY_ID}/duplicate"), duplicate),
            ("GET", re.compile(r"/api/profiles"), profiles),
            ("POST", re.compile(r"/api/checks"), start_check),
            ("GET", re.compile(r"/api/checks"), latest_check),
            ("GET", re.compile(rf"/api/checks/{ENTRY_ID}"), check),
        ]


class _PayloadTooLarge(Exception):
    """Raised when a request body exceeds ``MAX_BODY_BYTES``."""


def serve(
    workspace: Workspace,
    host: str,
    port: int,
    web_config: Path,
) -> int:
    """Run the web interface until interrupted; returns the exit code."""
    try:
        token = load_web_token(web_config)
    except (OSError, ValueError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1
    if not is_loopback(host) and not token:
        print(
            f"Configuration error: {host} is reachable from the network, so an access token "
            f"is required. Create {web_config} with:\n"
            f"  mkdir -p {web_config.parent} && python3 -c \"import json,secrets;"
            f"print(json.dumps({{'token': secrets.token_urlsafe(24)}}))\" > {web_config}"
            f" && chmod 600 {web_config}",
            file=sys.stderr,
        )
        return 1
    try:
        server = WebServer((host, port), workspace, token)
    except OSError as error:
        print(f"Server error: {error}", file=sys.stderr)
        return 1

    shown_host = "localhost" if host in ("127.0.0.1", "::1") else host
    print(f"firmAware web interface: http://{shown_host}:{server.server_address[1]}/")
    if token:
        print("Access token required: open the address once with ?token=<token from web.json>")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0
