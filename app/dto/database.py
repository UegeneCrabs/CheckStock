from dataclasses import dataclass, field
from typing import NotRequired, TypedDict


class PostgresServerSettings(TypedDict):
    application_name: str


class PostgresConnectArguments(TypedDict):
    dsn: str
    timeout: NotRequired[float | None]
    server_settings: NotRequired[PostgresServerSettings]


@dataclass(frozen=True, slots=True, kw_only=True)
class PostgresConnectionOptions:
    dsn: str = field(repr=False)
    connect_timeout: int | None = None
    application_name: str | None = None

    def connect_arguments(self) -> PostgresConnectArguments:
        arguments: PostgresConnectArguments = {"dsn": self.dsn}
        if self.connect_timeout is not None:
            arguments["timeout"] = float(self.connect_timeout) if self.connect_timeout > 0 else None
        if self.application_name is not None:
            arguments["server_settings"] = {"application_name": self.application_name}
        return arguments
