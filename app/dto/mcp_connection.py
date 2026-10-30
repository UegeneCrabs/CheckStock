from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel


class McpProvider(StrEnum):
    CLAUDE = "claude"
    CHATGPT = "chatgpt"
    UNKNOWN = "unknown"


def provider_for_redirects(redirects: list[str]) -> McpProvider:
    hosts = {urlsplit(uri).hostname for uri in redirects}
    if hosts == {"claude.ai"}:
        return McpProvider.CLAUDE
    if hosts == {"chatgpt.com"}:
        return McpProvider.CHATGPT
    return McpProvider.UNKNOWN


class McpConnection(BaseModel):
    id: str
    name: str
    created: float
    expires: float
    provider: McpProvider
