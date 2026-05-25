from __future__ import annotations

import ipaddress
import logging
import os
import socket
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

_LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}
_HOST_ALIASES_ENV = "RETROGUIDE_HOST_ALIASES"
_logger = logging.getLogger(__name__)


def read_text(source: str, *, timeout: int) -> str:
    request = Request(source)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except URLError as original_exc:
        parsed = urlsplit(source)
        hostname = parsed.hostname
        fallback_requests: list[Request] = []
        alias_fallback = _build_alias_fallback_request(source, original_exc)
        if alias_fallback is not None:
            fallback_requests.append(alias_fallback)
        docker_fallback = _build_docker_fallback_request(source, original_exc)
        if docker_fallback is not None and all(
            docker_fallback.full_url != fallback_request.full_url for fallback_request in fallback_requests
        ):
            fallback_requests.append(docker_fallback)
        if not fallback_requests:
            if hostname and _is_dns_resolution_error(original_exc):
                _add_hostname_resolution_guidance(original_exc, hostname)
            raise
        for fallback in fallback_requests:
            try:
                with urlopen(fallback, timeout=timeout) as response:
                    return response.read().decode("utf-8", errors="replace")
            except URLError:
                continue
        if hostname and _is_dns_resolution_error(original_exc):
            _add_hostname_resolution_guidance(original_exc, hostname)
        raise original_exc from None


def read_text_or_file(source: str, *, timeout: int) -> str:
    if source.startswith("http://") or source.startswith("https://"):
        return read_text(source, timeout=timeout)
    return Path(source).read_text(encoding="utf-8", errors="replace")


def _build_docker_fallback_request(source: str, error: URLError) -> Request | None:
    parsed = urlsplit(source)
    hostname = parsed.hostname
    if not hostname or not _is_running_in_docker() or not _should_try_fallback_host(hostname):
        return None
    is_loopback = hostname.lower().strip() in _LOOPBACK_HOSTNAMES
    if not _is_dns_resolution_error(error) and not (is_loopback and _is_connection_refused(error)):
        return None
    fallback_url = _replace_hostname(source, "host.docker.internal")
    host_header = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
    return Request(fallback_url, headers={"Host": host_header})


def _build_alias_fallback_request(source: str, error: URLError) -> Request | None:
    if not _is_dns_resolution_error(error):
        return None
    parsed = urlsplit(source)
    hostname = parsed.hostname
    if not hostname:
        return None
    alias_target = _get_alias_target(hostname)
    if not alias_target:
        return None
    fallback_url = _replace_hostname(source, alias_target)
    host_header = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
    return Request(fallback_url, headers={"Host": host_header})


def _get_alias_target(hostname: str) -> str | None:
    aliases = _parse_host_aliases(os.getenv(_HOST_ALIASES_ENV, ""))
    normalized = hostname.lower().strip()
    target = aliases.get(normalized)
    if not target:
        return None
    if target.lower().strip() == normalized:
        return None
    return target


def _parse_host_aliases(raw_value: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not raw_value:
        return aliases
    for entry in raw_value.split(","):
        token = entry.strip()
        if not token:
            continue
        if "=" not in token:
            _logger.warning("Ignoring invalid %s entry: %r", _HOST_ALIASES_ENV, token)
            continue
        source_host, target_host = (part.strip() for part in token.split("=", 1))
        if not source_host or not target_host:
            _logger.warning("Ignoring invalid %s entry: %r", _HOST_ALIASES_ENV, token)
            continue
        if any(ch in target_host for ch in "/?#") or "://" in target_host:
            _logger.warning("Ignoring invalid %s entry: %r", _HOST_ALIASES_ENV, token)
            continue
        aliases[source_host.lower()] = target_host
    return aliases


def _add_hostname_resolution_guidance(error: URLError, hostname: str) -> None:
    guidance = (
        f"Unable to resolve hostname '{hostname}' from inside the container. "
        f"Use an IP address, configure container DNS, add a Docker extra_hosts entry, "
        f"or set {_HOST_ALIASES_ENV}={hostname}=<ip-address>."
    )
    reason = str(error.reason).strip() if error.reason else ""
    if guidance in reason:
        return
    error.reason = f"{reason}. {guidance}" if reason else guidance


def _is_running_in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _should_try_fallback_host(hostname: str) -> bool:
    normalized = hostname.lower().strip()
    if normalized == "host.docker.internal":
        return False
    try:
        ipaddress.ip_address(normalized.strip("[]"))
        # Only skip non-loopback IPs; loopback IPs should fall through to the
        # loopback-hostname check handled by the caller.
        return normalized in _LOOPBACK_HOSTNAMES
    except ValueError:
        return True


def _is_dns_resolution_error(error: URLError) -> bool:
    reason = error.reason
    if isinstance(reason, socket.gaierror):
        return True
    message = str(reason).lower()
    return (
        "name or service not known" in message
        or "nodename nor servname provided" in message
        or "temporary failure in name resolution" in message
        or "no address associated with hostname" in message
    )


def _is_connection_refused(error: URLError) -> bool:
    reason = error.reason
    if isinstance(reason, ConnectionRefusedError):
        return True
    message = str(reason).lower()
    return "connection refused" in message


def _replace_hostname(source: str, replacement_host: str) -> str:
    parsed = urlsplit(source)
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo = f"{userinfo}:{parsed.password}"
        userinfo = f"{userinfo}@"
    host = replacement_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port_suffix = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{userinfo}{host}{port_suffix}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
