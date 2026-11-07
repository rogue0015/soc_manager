"""Shared types for platform adapters.

Adding a platform = add a PlatformInfo to the registry and, once it can
post, a Publisher subclass. Nothing else in the app needs to change.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

URL_RE = re.compile(r"https?://\S+")


class PublishError(Exception):
    """Raised when a platform rejects a post. The message is shown to the user."""


@dataclass(frozen=True)
class PublishResult:
    external_id: str
    external_url: str | None = None


@dataclass(frozen=True)
class AccountStatus:
    connected: bool          # credentials are valid
    can_post: bool           # posting is expected to work
    account: str | None      # e.g. "@Rogue0015"
    message: str             # plain-language explanation for the UI


class Publisher(ABC):
    @abstractmethod
    def publish(self, text: str) -> PublishResult: ...

    @abstractmethod
    def check(self) -> AccountStatus: ...


@dataclass(frozen=True)
class PlatformInfo:
    id: str
    name: str
    color: str           # calendar colour; chosen to be distinct, not brand-exact
    text_color: str      # readable text on top of `color`
    char_limit: int
    url_weight: int | None = None  # platforms that shorten links count each URL as this many chars

    def text_length(self, text: str) -> int:
        if self.url_weight is None:
            return len(text)
        return len(URL_RE.sub("x" * self.url_weight, text))
