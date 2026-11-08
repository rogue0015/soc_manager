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
    warning: str | None = None   # it went out, but an extra step (e.g. the first comment) didn't


@dataclass(frozen=True)
class ImageFile:
    data: bytes
    mime: str                    # image/jpeg | image/png | image/gif
    alt: str = ""


@dataclass(frozen=True)
class LinkCard:
    """A link shown as a preview card: title, summary and thumbnail."""
    url: str
    title: str
    description: str = ""
    thumbnail: ImageFile | None = None


@dataclass(frozen=True)
class Article:
    """A whole blog post, for platforms that publish articles instead of short posts (dev.to)."""
    title: str
    markdown: str
    canonical_url: str           # the blog post: the original, for search engines
    description: str = ""
    tags: tuple[str, ...] = ()   # as on the blog; each platform adapts them to its own rules


@dataclass(frozen=True)
class PostExtras:
    """Everything that goes with the text. Platforms use what they support (see PlatformInfo.features)."""
    visibility: str = "PUBLIC"   # PUBLIC | CONNECTIONS
    allow_reshare: bool = True
    image: ImageFile | None = None
    link_card: LinkCard | None = None
    first_comment: str = ""
    article: Article | None = None   # set for platforms with the "article" feature
    draft: bool = False              # save it as a draft on the platform instead of publishing


# Post metrics a platform can report. Keys match the post_metrics table.
METRICS = ("impressions", "members_reached", "reactions", "comments", "reshares",
           "saves", "link_clicks", "clicks", "engagements")


@dataclass(frozen=True)
class AccountProfile:
    """Who you're signed in as, for the Social tab. Public profile details only; None = not shared."""
    name: str
    handle: str | None = None      # "@rogue0015"
    url: str | None = None         # the profile page
    avatar: str | None = None      # picture URL on the platform's own servers
    followers: int | None = None
    following: int | None = None
    posts: int | None = None
    bio: str = ""


@dataclass(frozen=True)
class AccountStatus:
    connected: bool          # credentials are valid
    can_post: bool           # posting is expected to work
    account: str | None      # e.g. "@Rogue0015"
    message: str             # plain-language explanation for the UI


class Publisher(ABC):
    @abstractmethod
    def publish(self, text: str, extras: PostExtras | None = None) -> PublishResult: ...

    @abstractmethod
    def check(self) -> AccountStatus: ...

    # Optional actions on posts that are already live. Only platforms whose
    # PlatformInfo.features lists them override these.

    def profile(self) -> AccountProfile:
        """The signed-in account's public profile. Makes network calls; posts nothing."""
        raise PublishError("This platform doesn't share profile details with the app.")

    def delete(self, external_id: str) -> None:
        raise PublishError("This platform can't delete posts from the app.")

    def edit(self, external_id: str, text: str) -> None:
        raise PublishError("This platform can't edit published posts from the app.")

    stats_off_reason = "This platform doesn't share post statistics with the app."

    def stats_enabled(self) -> bool:
        return False

    def metrics(self, external_id: str) -> dict[str, int]:
        raise PublishError("This platform doesn't share post statistics with the app.")


@dataclass(frozen=True)
class Credential:
    key: str     # variable name in the env file
    label: str   # what the platform's developer portal calls it


@dataclass(frozen=True)
class Connector:
    """How a platform gets connected, for the connector cards on the Status tab.

    Holds env key *names* only, never values (see connectors.py).
    """
    auth: str                               # how it signs in, in plain words
    portal_url: str | None = None           # where the keys come from
    credentials: tuple[Credential, ...] = ()
    sign_in: str | None = None              # extra step when keys alone can't post (LinkedIn)
    token_key: str | None = None            # env key that sign-in writes
    expires_key: str | None = None          # env key with that token's expiry (UTC ISO)
    built: bool = False                     # an adapter exists and can post
    note: str = ""                          # what to know before connecting


@dataclass(frozen=True)
class PlatformInfo:
    id: str
    name: str
    color: str           # calendar colour; chosen to be distinct, not brand-exact
    text_color: str      # readable text on top of `color`
    char_limit: int
    url_weight: int | None = None  # platforms that shorten links count each URL as this many chars
    # What the app can do on this platform beyond posting text:
    # edit, delete, image, link_card, first_comment, visibility, stats,
    # article (publishes the whole blog post instead of the post text), draft,
    # handoff (its own scheduler can post it: the app opens compose_url), stats_import (analytics file)
    features: tuple[str, ...] = ()
    compose_url: str | None = None   # the platform's own post box, for handoff; {text} = URL-encoded text

    def text_length(self, text: str) -> int:
        if self.url_weight is None:
            return len(text)
        return len(URL_RE.sub("x" * self.url_weight, text))
