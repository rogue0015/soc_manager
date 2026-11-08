# 0007: Publish blog posts to dev.to as whole articles, from the app

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The owner wants their blog posts on dev.to "as they are", chosen post by post, and
driven from this app rather than by hand. dev.to is unlike the other platforms: it
takes an article (title, Markdown body, tags), not a short post, and it can mark the
original as canonical so search engines credit the blog.

Constraints we found:

- dev.to's API (Forem v1) authenticates with a personal API key (`api-key` header), has
  create (`POST /api/articles`), update (`PUT /api/articles/{id}`) and list
  (`GET /api/articles/me/all`), but **no delete**. Tags must be lowercase letters and
  digits, at most four. Articles are run through Liquid, so a literal `{{` or `{%` breaks them.
- Only one of the 19 blog posts still has its Markdown source on this machine; the blog
  keeps sources offline. Every post exists as its generated HTML page.
- Every post's `og:image` is the same author photo, which makes a poor article banner.

## Options considered

1. **dev.to's own RSS import** (Settings → Extensions): no code, but it imports every post in
   the feed as a draft, converts HTML on dev.to's side, and is controlled from dev.to,
   not this app. No stats here.
2. **A script in the blog repo**: quick, but outside the app: no calendar, no activity log,
   no stats, and it would need its own copy of the key handling.
3. **dev.to as a platform in the app** (chosen): blog posts are scheduled for dev.to like any
   other platform; when due, the app reads the published post, converts it back to Markdown
   and publishes it through the API.

For the article text:

- **Read the RSS feed's full content**: only the newest 20 posts are in the feed.
- **Fetch the post page and convert `<div class="post-body">` to Markdown** (chosen): works
  for every post and always matches what's live. The blog's builder emits a small, known
  set of tags, so a small converter on the standard library is enough; no new dependency.
  Converting the published *Icarus* page back gave the same 1,115 words as its source.

## Decision

dev.to is a registered platform (`devto`) with the features `article`, `draft` and
`stats`. An `article` platform publishes `PostExtras.article` (the blog post) instead of the
post text, so it only accepts posts made from a blog post (`validate_source`), takes no test
post, and has no own text. The article is read fresh from the blog when the post goes out
(`services/article.py`, via `Attachments`), with the blog URL as `canonical_url`, the blog's
tags adapted to dev.to's rules, and no cover image.

Per post the owner chooses **publish** or **save as draft** (option `draft`). Before creating,
the adapter looks for an article with the same canonical URL and updates it instead, so
sending a post twice, an earlier RSS import, or a publish cut off mid-way never makes a
duplicate; an update never unpublishes a live article. A composer preview shows the exact
Markdown dev.to will get.

Stats: `page_views_count`, `public_reactions_count` and `comments_count` from
`/articles/me/all` are saved as impressions, reactions and comments through the existing
**Refresh stats**. Page views go in the `impressions` column rather than a new one.

## Consequences

- **Plan selected** schedules blog posts on dev.to at a chosen spacing; the scheduler sends
  them, so cross-posting needs no manual step after planning.
- Editing a blog post and sending it to dev.to again updates the dev.to copy.
- If the blog is down when a post is due, the dev.to delivery fails with a readable reason
  and can be retried.
- A draft counts as *published* in Analytics (the app did its part); its warning says it's
  a draft on dev.to.
- No delete or edit from the app: dev.to has no delete API, and editing means changing the
  blog post and sending it again.
- The converter must grow if the blog's builder starts emitting new tags (tables, figures);
  until then unknown tags keep their text.
- Not done (ask first): checking the blog on a timer and sending every new post to dev.to
  without a per-post choice.
