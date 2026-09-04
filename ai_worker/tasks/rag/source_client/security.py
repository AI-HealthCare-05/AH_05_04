"""Security guards for external RAG source requests."""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import urljoin, urlsplit

from ai_worker.tasks.rag.source_client.contracts import (
    EndpointContract,
    SourceClientLimits,
    SourceFailureCode,
)

type HostResolver = Callable[[str], Awaitable[Sequence[str]]]


class SourceSecurityError(ValueError):
    """Sanitized security error that never exposes a request URL."""

    def __init__(
        self,
        code: SourceFailureCode,
        safe_message: str,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


async def resolve_host(host: str) -> tuple[str, ...]:
    """Resolve a host without exposing it through raised errors."""

    loop = asyncio.get_running_loop()

    try:
        address_info = await loop.getaddrinfo(
            host,
            443,
            type=socket.SOCK_STREAM,
        )
    except OSError as error:
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination could not be verified.",
        ) from error

    addresses = {str(sockaddr[0]) for _family, _type, _protocol, _canonical_name, sockaddr in address_info}

    if not addresses:
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination could not be verified.",
        )

    return tuple(sorted(addresses))


def _is_blocked_ip(address: str) -> bool:
    try:
        parsed_address = ipaddress.ip_address(address)
    except ValueError as error:
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination resolved to an invalid address.",
        ) from error

    if isinstance(parsed_address, ipaddress.IPv6Address):
        mapped_address = parsed_address.ipv4_mapped
        if mapped_address is not None:
            parsed_address = mapped_address

    return (
        parsed_address.is_private
        or parsed_address.is_loopback
        or parsed_address.is_link_local
        or parsed_address.is_multicast
        or parsed_address.is_reserved
        or parsed_address.is_unspecified
    )


def _path_matches_template(
    path: str,
    path_template: str,
) -> bool:
    path_segments = path.strip("/").split("/")
    template_segments = path_template.strip("/").split("/")

    if len(path_segments) != len(template_segments):
        return False

    for actual, expected in zip(path_segments, template_segments, strict=True):
        is_placeholder = expected.startswith("{") and expected.endswith("}")

        if not is_placeholder and actual != expected:
            return False

        if is_placeholder and not actual:
            return False

    return True


async def validate_operation_url(
    url: str,
    *,
    contract: EndpointContract,
    resolver: HostResolver = resolve_host,
) -> None:
    """Validate scheme, host, port, path and every resolved address."""

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination is invalid.",
        ) from error

    if parsed.scheme.lower() != "https":
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination must use HTTPS.",
        )

    if parsed.username is not None or parsed.password is not None:
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination must not contain user information.",
        )

    host = parsed.hostname

    if host is None or host.lower() != contract.host.lower():
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination host is not approved.",
        )

    if port not in (None, 443):
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination port is not approved.",
        )

    if parsed.fragment:
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination must not contain a fragment.",
        )

    if not _path_matches_template(parsed.path, contract.path_template):
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination path is not approved.",
        )

    addresses = await resolver(host)

    if not addresses or any(_is_blocked_ip(address) for address in addresses):
        raise SourceSecurityError(
            SourceFailureCode.DESTINATION_REJECTED,
            "Source destination address is not approved.",
        )


async def validate_redirect_location(
    *,
    current_url: str,
    location: str,
    hop_number: int,
    contract: EndpointContract,
    resolver: HostResolver = resolve_host,
) -> str:
    """Resolve and validate one redirect before the next request."""

    if hop_number > contract.limits.max_redirects:
        raise SourceSecurityError(
            SourceFailureCode.REDIRECT_REJECTED,
            "Source redirect limit was exceeded.",
        )

    redirected_url = urljoin(current_url, location)

    try:
        await validate_operation_url(
            redirected_url,
            contract=contract,
            resolver=resolver,
        )
    except SourceSecurityError as error:
        raise SourceSecurityError(
            SourceFailureCode.REDIRECT_REJECTED,
            "Source redirect destination is not approved.",
        ) from error

    return redirected_url


def validate_response_content_type(
    content_type: str | None,
    *,
    contract: EndpointContract,
) -> str:
    """Allow only content types frozen in the endpoint contract."""

    if content_type is None:
        raise SourceSecurityError(
            SourceFailureCode.CONTENT_TYPE_MISMATCH,
            "Source response content type is missing.",
        )

    media_type = content_type.partition(";")[0].strip().lower()
    allowed_types = {allowed.partition(";")[0].strip().lower() for allowed in contract.allowed_content_types}

    if media_type not in allowed_types:
        raise SourceSecurityError(
            SourceFailureCode.CONTENT_TYPE_MISMATCH,
            "Source response content type is not approved.",
        )

    return media_type


def validate_body_sizes(
    *,
    raw_size: int,
    decompressed_size: int,
    limits: SourceClientLimits,
) -> None:
    """Enforce both transferred and decompressed response size limits."""

    if raw_size > limits.max_response_bytes:
        raise SourceSecurityError(
            SourceFailureCode.RESPONSE_TOO_LARGE,
            "Source response exceeded the transfer size limit.",
        )

    if decompressed_size > limits.max_decompressed_bytes:
        raise SourceSecurityError(
            SourceFailureCode.RESPONSE_TOO_LARGE,
            "Source response exceeded the decompressed size limit.",
        )


_UNSAFE_XML_PATTERN = re.compile(
    rb"<!\s*(?:DOCTYPE|ENTITY)\b",
    flags=re.IGNORECASE,
)


def reject_unsafe_xml(body: bytes) -> None:
    """Reject XML declarations that may enable DTD or entity expansion."""

    if _UNSAFE_XML_PATTERN.search(body):
        raise SourceSecurityError(
            SourceFailureCode.XML_SECURITY_VIOLATION,
            "Source XML contains a forbidden declaration.",
        )
