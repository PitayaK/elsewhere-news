#!/usr/bin/env python3
"""Elsewhere's read-only OAuth client. Python 3.10+, standard library only."""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import hmac
import http.server
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass


VERSION = "3.1.0"
SERVICE = "news.elsewhere.native-oauth.v1"
SCOPES = "elsewhere.read offline_access"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
TOOLS = frozenset((
    "search_chunks", "find_entity", "search_entities", "get_entity_card",
    "traverse", "list_relation_keys", "get_content", "search_topics",
    "get_topic", "list_personas", "get_persona", "get_my_context",
    "get_my_content_views", "get_my_annotations", "get_my_sessions",
    "get_session", "get_my_topics", "whats_new", "check_account_binding",
))
MAX_RESPONSE = 4 * 1024 * 1024
MAX_INPUT = 32 * 1024
SENSITIVE_KEYS = frozenset(("access_token", "refresh_token", "code_verifier",
    "client_secret", "authorization", "completion_ticket", "poll_secret"))


class ClientError(Exception):
    def __init__(self, code: str, *, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class Profile:
    name: str
    resource: str
    issuer: str

    @property
    def resource_metadata(self):
        return self.resource.removesuffix("/mcp") + "/.well-known/oauth-protected-resource/mcp"

    @property
    def server_metadata(self):
        return self.issuer + "/.well-known/oauth-authorization-server"


PROFILES = {
    "production": Profile("production", "https://elsewhere.news/mcp", "https://auth.elsewhere.news"),
    "staging": Profile("staging", "https://elsewhere-v3-staging.vercel.app/mcp",
                       "https://elsewhere-agent-auth-staging.vercel.app"),
}


@dataclass
class HttpResponse:
    status: int
    headers: dict
    body: bytes

    def __post_init__(self):
        self.headers = {str(key).lower(): value for key, value in self.headers.items()}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpTransport:
    def __init__(self, timeout=35):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request(self, method, url, *, headers=None, body=None):
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (OSError, urllib.error.URLError, TimeoutError):
            raise ClientError("network_error") from None
        try:
            payload = response.read(MAX_RESPONSE + 1)
            if len(payload) > MAX_RESPONSE:
                raise ClientError("response_too_large")
            status_code = response.status
            headers_out = {key.lower(): value for key, value in response.headers.items()}
        except (OSError, TimeoutError):
            raise ClientError("network_error") from None
        finally:
            response.close()
        if 300 <= status_code < 400:
            raise ClientError("redirect_rejected")
        return HttpResponse(status_code, headers_out, payload)


def _json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ClientError("invalid_response")
            result[key] = value
        return result
    try:
        result = json.loads(body.decode("utf-8"), object_pairs_hook=unique,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, TypeError):
        raise ClientError("invalid_response") from None
    if not isinstance(result, dict):
        raise ClientError("invalid_response")
    return result


def _safe(value, secret_values=()):
    if isinstance(value, dict):
        return {_safe(str(key), secret_values): "[redacted]" if str(key).lower() in SENSITIVE_KEYS else _safe(item, secret_values)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item, secret_values) for item in value]
    if isinstance(value, str):
        for secret in secret_values:
            if isinstance(secret, str) and len(secret) >= 8:
                value = value.replace(secret, "[redacted]")
        value = value.encode("utf-8", errors="replace").decode("utf-8")
    return value


def _open_default_browser(url):
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(["/usr/bin/open", url], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        return result.returncode == 0
    except Exception:
        return False


class MacKeychainStore:
    name = "macos-keychain"

    def __init__(self, service=SERVICE, account="production"):
        self.service, self.account = service, account
        self._security = self._cf = None

    def _framework(self):
        if self._security is not None:
            return
        if sys.platform != "darwin":
            raise ClientError("store_unavailable")
        try:
            security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            P, U, I = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
            security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
            security.SecKeychainSetUserInteractionAllowed.restype = I
            security.SecKeychainCopyDefault.argtypes = [ctypes.POINTER(P)]
            security.SecKeychainCopyDefault.restype = I
            security.SecKeychainGetStatus.argtypes = [P, ctypes.POINTER(U)]
            security.SecKeychainGetStatus.restype = I
            security.SecKeychainFindGenericPassword.argtypes = [P, U, P, U, P,
                ctypes.POINTER(U), ctypes.POINTER(P), ctypes.POINTER(P)]
            security.SecKeychainFindGenericPassword.restype = I
            security.SecKeychainAddGenericPassword.argtypes = [P, U, P, U, P, U, P, ctypes.POINTER(P)]
            security.SecKeychainAddGenericPassword.restype = I
            security.SecKeychainItemModifyAttributesAndData.argtypes = [P, P, U, P]
            security.SecKeychainItemModifyAttributesAndData.restype = I
            security.SecKeychainItemDelete.argtypes = [P]
            security.SecKeychainItemDelete.restype = I
            security.SecKeychainItemFreeContent.argtypes = [P, P]
            security.SecKeychainItemFreeContent.restype = I
            cf.CFRelease.argtypes = [P]
            cf.CFRelease.restype = None
            self._security, self._cf = security, cf
        except (OSError, AttributeError):
            raise ClientError("store_unavailable") from None

    def _check(self, status):
        if status == 0:
            return
        if status in (-25308, -25293, -128):
            raise ClientError("store_locked")
        raise ClientError("store_unavailable")

    def _open(self):
        self._framework()
        self._check(self._security.SecKeychainSetUserInteractionAllowed(False))
        handle, flags = ctypes.c_void_p(), ctypes.c_uint32()
        self._check(self._security.SecKeychainCopyDefault(ctypes.byref(handle)))
        try:
            self._check(self._security.SecKeychainGetStatus(handle, ctypes.byref(flags)))
            if flags.value & 7 != 7:
                raise ClientError("store_locked")
        except Exception:
            self._cf.CFRelease(handle)
            raise
        return handle

    def _find(self, handle, account):
        service, user = self.service.encode(), account.encode()
        length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        status_code = self._security.SecKeychainFindGenericPassword(
            handle, len(service), service, len(user), user,
            ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
        if status_code == -25300:
            return None, None
        self._check(status_code)
        try:
            if length.value > 65536:
                raise ClientError("store_invalid")
            payload = ctypes.string_at(data, length.value)
        except Exception:
            if item:
                self._cf.CFRelease(item)
            raise
        finally:
            self._security.SecKeychainItemFreeContent(None, data)
        return payload, item

    def _write(self, handle, account, payload):
        old, item = self._find(handle, account)
        try:
            if item:
                self._check(self._security.SecKeychainItemModifyAttributesAndData(
                    item, None, len(payload), payload))
            else:
                service, user, created = self.service.encode(), account.encode(), ctypes.c_void_p()
                self._check(self._security.SecKeychainAddGenericPassword(
                    handle, len(service), service, len(user), user, len(payload), payload,
                    ctypes.byref(created)))
                if created:
                    self._cf.CFRelease(created)
        finally:
            if item:
                self._cf.CFRelease(item)

    def prepare(self):
        handle = self._open()
        probe = self.account + ".probe." + secrets.token_hex(12)
        value = secrets.token_bytes(24)
        created, item = ctypes.c_void_p(), None
        try:
            service, account = self.service.encode(), probe.encode()
            self._check(self._security.SecKeychainAddGenericPassword(
                handle, len(service), service, len(account), account, len(value), value,
                ctypes.byref(created)))
            found, item = self._find(handle, probe)
            if found != value:
                raise ClientError("store_unavailable")
        finally:
            try:
                # Retain the created reference: even a failed read cannot strand a probe.
                if created:
                    self._check(self._security.SecKeychainItemDelete(created))
            finally:
                if item:
                    self._cf.CFRelease(item)
                if created:
                    self._cf.CFRelease(created)
                self._cf.CFRelease(handle)

    def load(self):
        handle = self._open()
        try:
            data, item = self._find(handle, self.account)
            try:
                return _json(data) if data is not None else None
            finally:
                if item:
                    self._cf.CFRelease(item)
        finally:
            self._cf.CFRelease(handle)

    def save(self, record):
        payload = json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode()
        if len(payload) > 65536:
            raise ClientError("store_invalid")
        handle = self._open()
        try:
            self._write(handle, self.account, payload)
        finally:
            self._cf.CFRelease(handle)

    def delete(self):
        handle = self._open()
        try:
            data, item = self._find(handle, self.account)
            if item:
                try:
                    self._check(self._security.SecKeychainItemDelete(item))
                finally:
                    self._cf.CFRelease(item)
        finally:
            self._cf.CFRelease(handle)


def _default_state_directory(profile):
    return (Path.home() / ("Library/Application Support" if sys.platform == "darwin" else ".local/share")
            / "elsewhere-news/native-v1" / profile.name)


def _private_directory(directory):
    """Open a private directory by descriptor; never follow an application symlink."""
    path = Path(directory)
    if (os.name != "posix" or not path.is_absolute() or ".." in path.parts
        or not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0)):
        raise ClientError("state_directory_unsafe")
    uid = os.getuid()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = None
    try:
        fd = os.open(path.anchor, flags)
        parts = path.parts[1:]
        if not parts:
            raise ClientError("state_directory_unsafe")
        for index, part in enumerate(parts):
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=fd)
            info = os.fstat(child)
            mode = stat.S_IMODE(info.st_mode)
            final = index == len(parts) - 1
            # Root-owned sticky temporary parents are safe only before the private leaf.
            sticky_parent = (not final and info.st_uid == 0 and bool(mode & stat.S_ISVTX))
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid)
                or (mode & 0o022 and not sticky_parent)
                or (final and (info.st_uid != uid or mode != 0o700))):
                os.close(child)
                raise ClientError("state_directory_unsafe")
            os.close(fd)
            fd = child
        return fd
    except ClientError:
        if fd is not None:
            os.close(fd)
        raise
    except OSError:
        if fd is not None:
            os.close(fd)
        raise ClientError("state_directory_unsafe") from None


def _private_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ClientError("store_unsafe")
    return info


class PosixPrivateFileStore:
    """Owner-only persistent OAuth state, not an encrypted or same-UID-isolated vault."""
    name = "posix-private-file"
    filename = "credentials.json"

    def __init__(self, directory):
        self.directory = Path(directory)

    def _open_record(self, directory, missing_ok=True):
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(self.filename, flags, dir_fd=directory)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise ClientError("store_invalid") from None
        except OSError:
            raise ClientError("store_unsafe") from None
        try:
            _private_file(fd)
        except Exception:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _write_all(fd, payload):
        view = memoryview(payload)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise ClientError("store_unavailable")
            view = view[count:]

    def prepare(self):
        directory = _private_directory(self.directory)
        probe = ".store-probe-" + secrets.token_hex(16)
        fd = None
        try:
            existing = self._open_record(directory)
            if existing is not None:
                os.close(existing)
            fd = os.open(probe, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=directory)
            _private_file(fd)
            marker = secrets.token_bytes(24)
            self._write_all(fd, marker)
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, len(marker) + 1) != marker:
                raise ClientError("store_unavailable")
            os.unlink(probe, dir_fd=directory)
            probe = None
            os.fsync(directory)
        except ClientError:
            raise
        except OSError:
            raise ClientError("store_unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)
            if probe is not None:
                try:
                    os.unlink(probe, dir_fd=directory)
                except FileNotFoundError:
                    pass
            os.close(directory)

    def load(self):
        directory = _private_directory(self.directory)
        fd = None
        try:
            fd = self._open_record(directory)
            if fd is None:
                return None
            with os.fdopen(fd, "rb", closefd=False) as stream:
                value = stream.read(65537)
            if len(value) > 65536:
                raise ClientError("store_invalid")
            return _json(value)
        except ClientError:
            raise
        except OSError:
            raise ClientError("store_unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)

    def save(self, record):
        try:
            value = json.dumps(record, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        except (TypeError, ValueError, UnicodeError):
            raise ClientError("store_invalid") from None
        if len(value) > 65536:
            raise ClientError("store_invalid")
        directory = _private_directory(self.directory)
        temporary = ".credentials-" + secrets.token_hex(16) + ".tmp"
        fd = None
        try:
            existing = self._open_record(directory)
            if existing is not None:
                os.close(existing)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=directory)
            _private_file(fd)
            self._write_all(fd, value)
            os.fsync(fd)
            # Recheck the destination before replacement; insecure credentials are never chmodded.
            existing = self._open_record(directory)
            if existing is not None:
                os.close(existing)
            os.replace(temporary, self.filename, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
            os.fsync(directory)
        except ClientError:
            raise
        except OSError:
            raise ClientError("store_unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            os.close(directory)

    def delete(self):
        directory = _private_directory(self.directory)
        try:
            fd = self._open_record(directory)
            if fd is not None:
                os.close(fd)
                os.unlink(self.filename, dir_fd=directory)
                os.fsync(directory)
        except ClientError:
            raise
        except OSError:
            raise ClientError("store_unavailable") from None
        finally:
            os.close(directory)


class UnavailableStore:
    name = "unavailable"
    def prepare(self):
        raise ClientError("store_unavailable")
    load = prepare
    save = lambda self, record: self.prepare()


def default_store(profile, directory=None):
    if sys.platform == "darwin":
        return MacKeychainStore(account=profile.name)
    if sys.platform.startswith("linux"):
        return PosixPrivateFileStore(directory if directory is not None else _default_state_directory(profile))
    return UnavailableStore()


class ProcessLock:
    def __init__(self, directory, timeout=10):
        self.directory, self.timeout, self.fd = Path(directory), timeout, None
        self.directory_fd = None

    def __enter__(self):
        try:
            import fcntl
            self.directory_fd = _private_directory(self.directory)
            flags = (os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_CLOEXEC", 0))
            try:
                self.fd = os.open("authorization.lock", flags | os.O_CREAT | os.O_EXCL,
                                  0o600, dir_fd=self.directory_fd)
            except FileExistsError:
                self.fd = os.open("authorization.lock", flags, dir_fd=self.directory_fd)
            _private_file(self.fd)
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ClientError("client_busy")
                    time.sleep(0.05)
            return self
        except ClientError:
            self.__exit__()
            raise
        except (OSError, ImportError):
            self.__exit__()
            raise ClientError("state_directory_unavailable") from None

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.directory_fd is not None:
            os.close(self.directory_fd)
            self.directory_fd = None


class CallbackListener:
    def __init__(self, state, issuer):
        self.state, self.issuer, self.results = state, issuer, queue.Queue(maxsize=1)
        outer = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if len(self.path) > 8192 or self.headers.get("Host") != outer.authority:
                    self.send_error(400)
                    return
                url = urllib.parse.urlsplit(self.path)
                values = urllib.parse.parse_qs(url.query, keep_blank_values=True)
                if (url.scheme or url.netloc or url.fragment or url.path != "/callback"
                    or set(values) - {"state", "iss", "code", "error", "error_description"}
                    or any(len(v) != 1 for v in values.values())):
                    self.send_error(400)
                    return
                def one(key):
                    return values.get(key, [""])[0]
                if not hmac.compare_digest(one("state"), outer.state) or one("iss") != outer.issuer:
                    self.send_error(400)
                    return
                error, code = one("error"), one("code")
                if error and code:
                    self.send_error(400)
                    return
                if error and not code:
                    value = {"error": "authorization_denied"}
                elif re.fullmatch(r"[A-Za-z0-9._~-]{16,512}", code):
                    value = {"code": code}
                else:
                    self.send_error(400)
                    return
                try:
                    outer.results.put_nowait(value)
                except queue.Full:
                    pass
                body = b"Authorization received. Return to your Agent."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        try:
            self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except OSError:
            raise ClientError("callback_unavailable") from None
        self.authority = "127.0.0.1:" + str(self.server.server_address[1])
        self.redirect_uri = "http://" + self.authority + "/callback"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def wait(self, timeout):
        try:
            value = self.results.get(timeout=timeout)
        except queue.Empty:
            raise ClientError("authorization_timeout") from None
        if value.get("error"):
            raise ClientError("authorization_denied")
        return value["code"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class SessionClient:
    def __init__(self, profile=PROFILES["production"], store=None, transport=None,
                 browser_opener=None, state_dir=None, emit=None, now=None,
                 authorization_timeout=300, callback_mode=None, sleep=None, monotonic=None):
        if profile not in PROFILES.values():
            raise ClientError("invalid_profile")
        self.profile = profile
        self.state_dir = Path(state_dir) if state_dir else _default_state_directory(profile)
        self.store = store if store is not None else default_store(profile, self.state_dir)
        self.transport = transport if transport is not None else HttpTransport()
        self.browser_opener = browser_opener if browser_opener is not None else _open_default_browser
        self.callback_mode = callback_mode if callback_mode is not None else "relay"
        if self.callback_mode not in ("relay", "loopback"):
            raise ClientError("invalid_callback_mode")
        self.sleep = sleep if sleep is not None else time.sleep
        self.monotonic = monotonic if monotonic is not None else time.monotonic
        self.emit = emit if emit is not None else lambda event: None
        self.now = now if now is not None else time.time
        self.authorization_timeout = authorization_timeout
        self.secret_values = []
        self._id = 0
        self._mcp_session_id = None
        self._protocol = PROTOCOLS[0]

    def _event(self, value):
        self.emit(_safe(value, self.secret_values))

    def _request_json(self, method, url, value=None, form=False):
        headers = {"Accept": "application/json"}
        body = None
        if value is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            body = urllib.parse.urlencode(value).encode() if form else json.dumps(value, separators=(",", ":")).encode()
        response = self.transport.request(method, url, headers=headers, body=body)
        if response.status != 200 and not (method == "POST" and response.status == 201):
            raise ClientError("authorization_failed" if response.status < 500 else "temporarily_unavailable")
        return _json(response.body)

    def _metadata(self):
        resource = self._request_json("GET", self.profile.resource_metadata)
        if resource.get("resource") != self.profile.resource or resource.get("authorization_servers") != [self.profile.issuer]:
            raise ClientError("metadata_mismatch")
        metadata = self._request_json("GET", self.profile.server_metadata)
        expected = {"issuer": self.profile.issuer,
            "authorization_endpoint": self.profile.issuer + "/oauth/authorize",
            "token_endpoint": self.profile.issuer + "/oauth/token",
            "registration_endpoint": self.profile.issuer + "/oauth/register"}
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise ClientError("metadata_mismatch")
        if (not metadata.get("authorization_response_iss_parameter_supported") is True
            or "S256" not in metadata.get("code_challenge_methods_supported", [])
            or "none" not in metadata.get("token_endpoint_auth_methods_supported", [])
            or not {"authorization_code", "refresh_token"}.issubset(set(metadata.get("grant_types_supported", [])))
            or not set(SCOPES.split()).issubset(set(metadata.get("scopes_supported", [])))):
            raise ClientError("metadata_mismatch")
        return metadata

    def _load(self):
        record = self.store.load()
        if record is None:
            return None
        if (not isinstance(record, dict) or record.get("schema") != 1
            or record.get("resource") != self.profile.resource
            or record.get("issuer") != self.profile.issuer
            or record.get("state") not in ("ready", "refresh_pending", "reauth_required")):
            raise ClientError("store_invalid")
        for key in ("access_token", "refresh_token"):
            value = record.get(key)
            if isinstance(value, str):
                self.secret_values.append(value)
        if record["state"] == "ready":
            if (not isinstance(record.get("expires_at"), (int, float))
                or isinstance(record.get("expires_at"), bool)
                or not math.isfinite(record["expires_at"])
                or not isinstance(record.get("client_id"), str)
                or not all(isinstance(record.get(key), str) and re.fullmatch(r"[A-Za-z0-9._~-]{16,8192}", record[key])
                           for key in ("access_token", "refresh_token"))):
                raise ClientError("store_invalid")
            try:
                uuid.UUID(record["client_id"])
                self._access_claims(record["access_token"])
            except (ClientError, ValueError, TypeError, AttributeError):
                raise ClientError("store_invalid") from None
        return record

    def _invalid(self, record):
        self.store.save({"schema": 1, "resource": self.profile.resource,
            "issuer": self.profile.issuer, "state": "reauth_required"})
        raise ClientError("reauth_required")

    def _access_claims(self, token):
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise ValueError()
            claims = _json(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            if (claims.get("iss") != self.profile.issuer
                or claims.get("aud") not in (self.profile.resource, [self.profile.resource])
                or not isinstance(claims.get("exp"), int) or isinstance(claims["exp"], bool)):
                raise ValueError()
            return claims
        except (ValueError, TypeError, ClientError):
            raise ClientError("invalid_token_response") from None

    def _tokens(self, response, client_id):
        if (not isinstance(response, dict) or response.get("token_type", "").lower() != "bearer"
            or set(str(response.get("scope", "")).split()) != set(SCOPES.split())
            or not isinstance(response.get("expires_in"), int)
            or isinstance(response.get("expires_in"), bool)
            or not 0 < response["expires_in"] <= 86400):
            raise ClientError("invalid_token_response")
        for key in ("access_token", "refresh_token"):
            value = response.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{16,8192}", value):
                raise ClientError("invalid_token_response")
            self.secret_values.append(value)
        token = response["access_token"]
        claims = self._access_claims(token)
        if claims["exp"] <= self.now():
            raise ClientError("invalid_token_response")
        # TLS/discovery bind the token exchange. The MCP server verifies JWT signatures.
        return {"schema": 1, "state": "ready", "resource": self.profile.resource,
            "issuer": self.profile.issuer, "client_id": client_id,
            "access_token": token, "refresh_token": response["refresh_token"],
            "expires_at": min(self.now() + response["expires_in"], claims["exp"])}

    def _register(self, redirect_uri):
        registered = self._request_json("POST", self.profile.issuer + "/oauth/register", {
            "client_name": "elsewhere-news", "client_uri": "https://elsewhere.news",
            "software_id": "elsewhere-news-cli", "software_version": VERSION,
            "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": SCOPES})
        client_id = registered.get("client_id")
        try:
            uuid.UUID(client_id)
        except (ValueError, TypeError, AttributeError):
            raise ClientError("invalid_registration_response") from None
        if (registered.get("token_endpoint_auth_method") != "none" or "client_secret" in registered
            or registered.get("redirect_uris") != [redirect_uri]
            or set(registered.get("grant_types", [])) != {"authorization_code", "refresh_token"}
            or registered.get("response_types") != ["code"]
            or set(str(registered.get("scope", "")).split()) != set(SCOPES.split())):
            raise ClientError("invalid_registration_response")
        return client_id

    def _authorization_url(self, client_id, redirect_uri, state, challenge):
        return self.profile.issuer + "/oauth/authorize?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "scope": SCOPES, "state": state, "resource": self.profile.resource,
            "code_challenge": challenge, "code_challenge_method": "S256"})

    def _show_authorization(self, url):
        try:
            opened = bool(self.browser_opener(url))
        except Exception:
            opened = False
        self._event({"event": "authorization_required", "profile": self.profile.name,
                     "authorization_url": url, "browser_opened": opened,
                     "callback_mode": self.callback_mode})

    def _relay_code(self, client_id, state, challenge):
        redirect_uri = self.profile.issuer + "/oauth/client/callback"
        poll_secret = secrets.token_urlsafe(32)
        self.secret_values.append(poll_secret)
        started_at = self.monotonic()
        flow = self._request_json("POST", self.profile.issuer + "/oauth/client/start", {
            "client_id": client_id, "code_challenge": challenge, "state": state,
            "poll_secret_hash": hashlib.sha256(poll_secret.encode()).hexdigest()})
        try:
            uuid.UUID(flow.get("flow_id"))
        except (ValueError, TypeError, AttributeError):
            raise ClientError("invalid_relay_response") from None
        if (flow.get("redirect_uri") != redirect_uri or type(flow.get("expires_in")) is not int
            or not 1 <= flow["expires_in"] <= 300 or type(flow.get("interval")) is not int
            or not 2 <= flow["interval"] <= 30):
            raise ClientError("invalid_relay_response")
        deadline = started_at + min(flow["expires_in"], self.authorization_timeout, 300)
        self._show_authorization(self._authorization_url(client_id, redirect_uri, state, challenge))
        interval = flow["interval"]
        while True:
            remaining = deadline - self.monotonic()
            if remaining < interval:
                raise ClientError("authorization_timeout")
            self.sleep(interval)
            if self.monotonic() >= deadline:
                raise ClientError("authorization_timeout")
            polled = self.transport.request("POST", self.profile.issuer + "/oauth/client/poll",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                body=json.dumps({"flow_id": flow["flow_id"], "poll_secret": poll_secret},
                                separators=(",", ":")).encode())
            if polled.status == 429:
                retry = self._retry_after(polled)
                interval = max(interval, min(60, max(2, retry if retry is not None else 2)))
                continue
            if polled.status != 200:
                raise ClientError("authorization_failed" if polled.status < 500 else "temporarily_unavailable")
            response = _json(polled.body)
            status = response.get("status")
            if status == "pending":
                next_interval = response.get("interval", interval)
                if type(next_interval) is not int or not 2 <= next_interval <= 30:
                    raise ClientError("invalid_relay_response")
                interval = max(interval, next_interval)
                continue
            if status == "denied":
                raise ClientError("authorization_denied")
            code = response.get("code")
            if (status != "complete" or response.get("iss") != self.profile.issuer
                or not isinstance(response.get("state"), str)
                or not hmac.compare_digest(response["state"], state)
                or not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{16,512}", code)):
                raise ClientError("invalid_relay_response")
            self.secret_values.append(code)
            return code

    def _authorize(self):
        self._metadata()
        verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        self.secret_values.append(verifier)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        callback = None
        try:
            if self.callback_mode == "relay":
                redirect_uri = self.profile.issuer + "/oauth/client/callback"
                client_id = self._register(redirect_uri)
                code = self._relay_code(client_id, state, challenge)
            else:
                callback = CallbackListener(state, self.profile.issuer)
                redirect_uri = callback.redirect_uri
                client_id = self._register(redirect_uri)
                self._show_authorization(self._authorization_url(client_id, redirect_uri, state, challenge))
                code = callback.wait(self.authorization_timeout)
            self.secret_values.append(code)
            response = self._request_json("POST", self.profile.issuer + "/oauth/token", {
                "grant_type": "authorization_code", "client_id": client_id, "code": code,
                "code_verifier": verifier, "redirect_uri": redirect_uri,
                "resource": self.profile.resource}, form=True)
            record = self._tokens(response, client_id)
            self.store.save(record)
            return record
        finally:
            if callback is not None:
                callback.close()

    def _ready(self, record, non_interactive=False):
        if record is None:
            if non_interactive:
                raise ClientError("reauth_required")
            return self._authorize()
        if record["state"] != "ready":
            raise ClientError("reauth_required")
        if record["expires_at"] > self.now() + 30:
            return record
        pending = dict(record, state="refresh_pending")
        self.store.save(pending)  # Durable, atomic marker precedes the rotating request.
        try:
            response = self._request_json("POST", self.profile.issuer + "/oauth/token", {
                "grant_type": "refresh_token", "client_id": record["client_id"],
                "refresh_token": record["refresh_token"], "resource": self.profile.resource}, form=True)
            updated = self._tokens(response, record["client_id"])
            if updated["refresh_token"] == record["refresh_token"]:
                raise ClientError("invalid_token_response")
            self.store.save(updated)
            return updated
        except Exception:
            # Never retry a rotating token, including after a lost response or failed save.
            raise ClientError("reauth_required") from None

    def _rpc(self, record, method, params=None, notification=False):
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method}
        if not notification:
            body["id"] = self._id
        if params is not None:
            body["params"] = params
        encoded = json.dumps(body, separators=(",", ":")).encode()
        if len(encoded) > MAX_INPUT:
            raise ClientError("input_too_large")
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "Authorization": "Bearer " + record["access_token"], "MCP-Protocol-Version": self._protocol}
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id
        response = self.transport.request("POST", self.profile.resource, headers=headers, body=encoded)
        if notification and response.status in (200, 202, 204):
            return None
        payload = None
        try:
            payload = self._rpc_payload(response, body.get("id"))
        except ClientError:
            if response.status == 200:
                raise
        if self._reauth_response(response, payload):
            self._invalid(record)
        if response.status in (401, 403):
            # A protection page is not evidence that the saved OAuth grant is revoked.
            raise ClientError("access_blocked")
        retry_after = self._retry_after(response)
        if response.status not in (200, 429, 503) or payload is None:
            if response.status == 429:
                raise ClientError("rate_limited", retry_after=retry_after)
            if response.status >= 500:
                raise ClientError("temporarily_unavailable", retry_after=retry_after)
            raise ClientError("mcp_request_failed")
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            if not isinstance(session_id, str) or not re.fullmatch(r"[\x21-\x7e]{1,256}", session_id):
                raise ClientError("invalid_response")
            self._mcp_session_id = session_id
        if "error" in payload:
            error = payload["error"]
            code = error.get("data", {}).get("code") if isinstance(error, dict) and isinstance(error.get("data"), dict) else None
            allowed = {"bad_request", "not_found", "rate_limited", "quota_exceeded",
                "entity_coverage_exceeded", "account_binding_mismatch", "response_too_large",
                "timeout", "temporarily_unavailable", "internal_error"}
            raise ClientError(code if isinstance(code, str) and code in allowed else "mcp_request_failed", retry_after=retry_after)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ClientError("invalid_response")
        if response.status != 200 and (method != "tools/call" or result.get("isError") is not True):
            raise ClientError("temporarily_unavailable" if response.status == 503 else "rate_limited",
                              retry_after=retry_after)
        if retry_after is not None:
            meta = result.get("_meta")
            if meta is None:
                result["_meta"] = meta = {}
            if isinstance(meta, dict):
                meta["elsewhere/retryAfterSeconds"] = retry_after
        return result

    @staticmethod
    def _retry_after(response):
        value = response.headers.get("retry-after", "")
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
            return min(int(value), 86400)
        return None

    def _bound_challenge(self, challenge):
        if not isinstance(challenge, str) or len(challenge) > 4096 or not challenge.startswith("Bearer "):
            return False
        fields = re.findall(r'([a-z_]+)="([^"\\]*)"', challenge)
        values = dict(fields)
        return (len(values) == len(fields) and values.get("resource_metadata") == self.profile.resource_metadata
                and values.get("error") in ("invalid_token", "insufficient_scope"))

    def _reauth_response(self, response, payload):
        if self._bound_challenge(response.headers.get("www-authenticate")):
            return True
        if not isinstance(payload, dict):
            return False
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("data"), dict) and error["data"].get("code") == "reauth_required":
            return True
        result = payload.get("result")
        if not isinstance(result, dict) or result.get("isError") is not True:
            return False
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and isinstance(structured.get("error"), dict) and structured["error"].get("code") == "reauth_required":
            return True
        meta = result.get("_meta")
        challenges = meta.get("mcp/www_authenticate") if isinstance(meta, dict) else None
        return (self._bound_challenge(challenges) or
                isinstance(challenges, list) and any(self._bound_challenge(value) for value in challenges))

    @staticmethod
    def _rpc_payload(response, request_id):
        if len(response.body) > MAX_RESPONSE:
            raise ClientError("response_too_large")
        if "text/event-stream" in response.headers.get("content-type", ""):
            messages = []
            data = []
            try:
                lines = response.body.decode("utf-8").splitlines()
            except UnicodeError:
                raise ClientError("invalid_response") from None
            for line in lines + [""]:
                if not line and data:
                    messages.append(_json("\n".join(data).encode()))
                    data = []
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
            matches = [item for item in messages if type(item.get("id")) is type(request_id) and item.get("id") == request_id]
            if len(matches) != 1:
                raise ClientError("invalid_response")
            payload = matches[0]
        else:
            payload = _json(response.body)
        if (payload.get("jsonrpc") != "2.0" or type(payload.get("id")) is not type(request_id)
            or payload.get("id") != request_id or ("result" in payload) == ("error" in payload)):
            raise ClientError("invalid_response")
        return payload

    def _initialize(self, record):
        self._mcp_session_id = None
        result = self._rpc(record, "initialize", {"protocolVersion": PROTOCOLS[0],
            "capabilities": {}, "clientInfo": {"name": "elsewhere-news", "version": VERSION}})
        if result.get("protocolVersion") not in PROTOCOLS:
            raise ClientError("unsupported_protocol")
        self._protocol = result["protocolVersion"]
        self._rpc(record, "notifications/initialized", notification=True)
        result = self._rpc(record, "tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list) or len(tools) != 19 or {item.get("name") for item in tools if isinstance(item, dict)} != TOOLS:
            raise ClientError("server_contract_mismatch")
        return tools

    def _enter(self):
        self.store.prepare()
        return self._load()

    def capabilities(self):
        try:
            self.store.prepare()
            available = True
            code = None
        except ClientError as error:
            available, code = False, error.code
        result = {"event": "capabilities", "profile": self.profile.name,
            "secure_store": self.store.name, "secure_store_available": available,
            "credential_protection": "owner-only-file" if isinstance(self.store, PosixPrivateFileStore) else self.store.name,
            "persistence_requires_host_retention": True,
            "callback_mode": self.callback_mode, "requires_local_callback": self.callback_mode == "loopback",
            "transport": "streamable_http", "tools": sorted(TOOLS)}
        if code:
            result["code"] = code
        return result

    def status(self, check=False):
        with ProcessLock(self.state_dir):
            record = self._enter()
            if record is None:
                return {"event": "status", "profile": self.profile.name, "status": "disconnected"}
            if record["state"] != "ready":
                raise ClientError("reauth_required")
            if check:
                record = self._ready(record, non_interactive=True)
                self._initialize(record)
                result = self._rpc(record, "tools/call", {"name": "list_relation_keys", "arguments": {}})
                if result.get("isError"):
                    raise ClientError("verification_failed")
            return {"event": "status", "profile": self.profile.name, "status": "connected" if check else "authorized",
                    "needs_refresh": record["expires_at"] <= self.now() + 30}

    def connect(self, reauthorize=False):
        with ProcessLock(self.state_dir):
            record = self._enter()
            if reauthorize or record is None or record["state"] != "ready":
                record = self._authorize()
            else:
                try:
                    record = self._ready(record, non_interactive=True)
                    self._verify(record)
                except ClientError as error:
                    if error.code != "reauth_required":
                        raise
                    record = self._authorize()
                else:
                    return self._connected()
            self._verify(record)
            return self._connected()

    def _verify(self, record):
        self._initialize(record)
        result = self._rpc(record, "tools/call", {"name": "list_relation_keys", "arguments": {}})
        if result.get("isError"):
            raise ClientError("verification_failed")

    def _connected(self):
        return {"event": "connected", "profile": self.profile.name, "status": "connected", "verified_tool": "list_relation_keys"}

    def list_tools(self, non_interactive=True):
        with ProcessLock(self.state_dir):
            record = self._ready(self._enter(), non_interactive)
            return self._initialize(record)

    def call(self, tool, args, non_interactive=False):
        if tool not in TOOLS:
            raise ClientError("unsupported_tool")
        if not isinstance(args, dict):
            raise ClientError("invalid_arguments")
        with ProcessLock(self.state_dir):
            record = self._ready(self._enter(), non_interactive)
            self._initialize(record)
            result = self._rpc(record, "tools/call", {"name": tool, "arguments": args})
            return _safe(result, self.secret_values)


def _parser():
    class Parser(argparse.ArgumentParser):
        def parse_args(self, args=None, namespace=None):
            values = super().parse_args(args, namespace)
            if not hasattr(values, "profile"):
                values.profile = "production"
            if not hasattr(values, "non_interactive"):
                values.non_interactive = False
            return values
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", choices=tuple(PROFILES), default=argparse.SUPPRESS)
    common.add_argument("--non-interactive", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--callback-mode", choices=("relay", "loopback"), default=argparse.SUPPRESS)
    parser = Parser(description=__doc__, parents=[common])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("capabilities", "status", "connect", "call"):
        command = commands.add_parser(name, parents=[common])
        if name == "status":
            command.add_argument("--check", action="store_true")
        if name == "connect":
            command.add_argument("--reauthorize", action="store_true")
        if name == "call":
            command.add_argument("tool", choices=sorted(TOOLS))
    return parser


def main(argv=None):
    def output(event):
        print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)
    try:
        args = _parser().parse_args(argv)
        client = SessionClient(PROFILES[args.profile], emit=output,
                               callback_mode=getattr(args, "callback_mode", None))
        if args.command == "capabilities":
            result = client.capabilities()
        elif args.command == "status":
            result = client.status(check=args.check)
        elif args.command == "connect":
            if args.non_interactive:
                result = client.status(check=True)
                if result["status"] != "connected":
                    raise ClientError("reauth_required")
            else:
                result = client.connect(reauthorize=args.reauthorize)
        else:
            body = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(body) > MAX_INPUT:
                raise ClientError("input_too_large")
            result = client.call(args.tool, _json(body), args.non_interactive)
            result = {"event": "tool_result", "profile": args.profile,
                      "status": "error" if result.get("isError") else "success", "data": result}
        output(result)
        return 1 if result.get("status") == "error" or result.get("secure_store_available") is False else 0
    except ClientError as error:
        event = {"event": "error", "code": error.code}
        if error.retry_after is not None:
            event["retry_after_seconds"] = error.retry_after
        output(event)
        return 1
    except KeyboardInterrupt:
        output({"event": "error", "code": "cancelled"})
        return 130
    except Exception:
        output({"event": "error", "code": "client_error"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
