"""Changes to posts that are already live on a platform: edit, delete, fetch statistics.

These act on the platform itself (LinkedIn), so every action checks that the
post really went out, wasn't deleted since, and that the platform supports it
(PlatformInfo.features). The planner keeps its record either way: a deleted
post is marked removed_at, an edited one gets its new text and edited_at.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..platforms import PLATFORMS, PublishError, Publisher
from .activity import ActivityLog, snippet
from .errors import Conflict, InvalidInput, NotFound
from .posts import PostService, validate_content


class LiveService:
    def __init__(self, posts: PostService, publishers: dict[str, Publisher], activity: ActivityLog):
        self.posts = posts
        self.publishers = publishers   # the shared dict: a sign-in can replace an adapter
        self.activity = activity

    def _live(self, post_id: int, platform: str, feature: str, verb: str) -> tuple[dict, dict, Publisher]:
        info = PLATFORMS.get(platform)
        if info is None:
            raise NotFound(f"Unknown platform: {platform}")
        post = self.posts.get(post_id)
        target = next((t for t in post["targets"] if t["platform"] == platform), None)
        if target is None:
            raise NotFound(f"This post isn't planned for {info.name}.")
        if target["status"] != "published" or not target["external_id"]:
            raise Conflict(f"This post isn't live on {info.name}, so there's nothing to {verb} there.")
        if target["removed_at"]:
            raise Conflict(f"This post was already deleted from {info.name}.")
        if feature not in info.features:
            raise InvalidInput(f"{info.name} can't {verb} posts from the app.")
        publisher = self.publishers.get(platform)
        if publisher is None:
            raise InvalidInput(f"{info.name} isn't connected. Sign in on the Status tab first.")
        return post, target, publisher

    def send_now(self, platform: str, text: str) -> dict[str, Any]:
        """Publish `text` right away (the test post). It's kept in the planner like any other post,
        so it can be edited or deleted later; LinkedIn offers no way to list your posts afterwards."""
        info = PLATFORMS.get(platform)
        if info is None:
            raise NotFound(f"Unknown platform: {platform}")
        if "article" in info.features:
            raise InvalidInput(f"{info.name} publishes whole blog posts, so it can't take a test post. "
                               "Schedule a blog post for it instead.")
        validate_content(text, [platform])
        publisher = self.publishers.get(platform)
        if publisher is None:
            raise InvalidInput(f"{info.name} is not connected yet.")
        try:
            result = publisher.publish(text)
        except PublishError as exc:
            raise InvalidInput(str(exc)) from exc
        post = self.posts.record_published(text, platform, result.external_id, result.external_url, result.warning)
        self.activity.record("test_post", f"Sent the test post {snippet(text)} to {info.name}.",
                             platform=platform, post_id=post["id"], url=result.external_url)
        return {**asdict(result), "post_id": post["id"]}

    def delete(self, post_id: int, platform: str) -> dict[str, Any]:
        """Delete the post on the platform. It can't be undone."""
        _post, target, publisher = self._live(post_id, platform, "delete", "delete")
        try:
            publisher.delete(target["external_id"])
        except PublishError as exc:
            raise InvalidInput(str(exc)) from exc
        self.posts.mark_removed(target["id"])
        self.activity.record("removed", f"Deleted {snippet(target['text'])} from {PLATFORMS[platform].name}.",
                             platform=platform, post_id=post_id)
        return self.posts.get(post_id)

    def edit(self, post_id: int, platform: str, text: str) -> dict[str, Any]:
        """Replace the text of the live post on the platform."""
        _post, target, publisher = self._live(post_id, platform, "edit", "edit")
        validate_content(text, [platform])
        if text == target["text"]:
            raise InvalidInput("That's the same text as the live post. Change something first.")
        try:
            publisher.edit(target["external_id"], text)
        except PublishError as exc:
            raise InvalidInput(str(exc)) from exc
        self.posts.set_live_text(target["id"], text)
        self.activity.record("edited", f"Edited {snippet(text)} on {PLATFORMS[platform].name}.",
                             platform=platform, post_id=post_id, url=target["external_url"])
        return self.posts.get(post_id)

    # ---- statistics ----

    def stats_status(self) -> list[dict[str, Any]]:
        """For each platform that can report statistics: is it connected, and are automatic stats on?"""
        out = []
        for pid, info in PLATFORMS.items():
            if "stats" not in info.features:
                continue
            pub = self.publishers.get(pid)
            enabled = bool(pub and pub.stats_enabled())
            reason = ("" if enabled else
                      f"{info.name} isn't connected." if pub is None else getattr(pub, "stats_off_reason", ""))
            out.append({"platform": pid, "connected": pub is not None, "automatic": enabled, "reason": reason})
        return out

    def refresh_stats(self) -> dict[str, Any]:
        """Fetch fresh statistics for every live post on platforms whose automatic stats are on."""
        status = self.stats_status()
        enabled = [s["platform"] for s in status if s["automatic"]]
        if not enabled:
            reasons = " ".join(s["reason"] for s in status if s["reason"])
            raise InvalidInput(reasons or "No connected platform shares post statistics.")
        updated, problems = 0, []
        for pid in enabled:
            publisher = self.publishers[pid]
            for t in self.posts.live_targets(pid):
                try:
                    self.posts.save_metrics(t["id"], "api", publisher.metrics(t["external_id"]))
                    updated += 1
                except PublishError as exc:
                    problems.append(f"{snippet(t['body'])}: {exc}")
        return {"updated": updated, "problems": problems[:10]}
