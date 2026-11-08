"""dev.to: a blog post as Markdown, the adapter, and the scheduling rules. dev.to is mocked; nothing is sent."""

from __future__ import annotations

from dataclasses import replace

import pytest
import requests
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.platforms import PLATFORMS, Article, PostExtras, PublishError, build_publisher
from backend.app.platforms.connectors import connector_status
from backend.app.platforms.devto import API, NOT_AN_ARTICLE, DevtoPublisher, devto_tags, escape_liquid
from backend.app.services.article import ArticleError, ArticleReader, html_to_markdown, post_body_markdown
from backend.app.services.attachments import Attachments
from backend.app.services.scheduler import publish_due

from .conftest import FakePublisher
from .test_blog import _seed
from .test_linkedin import NOW, FakeResponse
from .test_posts import force_due, future

PAGE_URL = "https://blog.test/blogs/one.html"
PAGE = """<!DOCTYPE html><html><head>
<title>One | Brandon</title>
<meta property="og:title" content="One">
<meta property="og:description" content="Summary of one.">
<meta property="og:url" content="https://blog.test/blogs/one.html">
<meta property="article:tag" content="tech-community">
<meta property="article:tag" content="ai">
</head><body>
<header class="site-head"><a href="../index.html">HOME</a></header>
<main><article class="post"><header class="post-head"><h1>One</h1></header>
<div class="post-body" itemprop="articleBody">
<p>Hey,</p>
<p>I made <a href="/tools/">small tools</a> and <a href="designing-vision-sl.html">Vision-SL</a>,
see <a href="https://x.test/a" target="_blank" rel="noopener">this</a>.</p>
<h2>The context</h2>
<ol><li>No compute.</li><li>No <em>data</em> kept.</li></ol>
<blockquote><p>Quote one.</p><p>Quote two.</p></blockquote>
<p>Stars * and under_scores stay literal.</p>
<hr>
<p>Signed</p>
</div>
</article></main>
<footer class="site-foot">FOOTER</footer>
<script>var x = "<p>not content</p>";</script>
</body></html>"""

EXPECTED = """Hey,

I made [small tools](https://blog.test/tools/) and [Vision-SL](https://blog.test/blogs/designing-vision-sl.html), \
see [this](https://x.test/a).

## The context

1. No compute.
2. No *data* kept.

> Quote one.
>
> Quote two.

Stars \\* and under\\_scores stay literal.

---

Signed
"""

ARTICLE = Article(title="One", markdown="Hello {{ name }}\n", canonical_url=PAGE_URL,
                  description="Summary", tags=("tech-community", "ai"))
KEY = {"DEVTO_API_KEY": "test-key"}


class Page:
    """A fetched web page, as requests.get returns it."""

    def __init__(self, text: str, status: int = 200):
        self.text, self.status_code = text, status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeDevto:
    """Stands in for the requests module: answers like dev.to and records every call."""

    def __init__(self, mine: list[dict] | None = None):
        self.mine = mine or []
        self.calls: list[tuple[str, str, dict]] = []
        self.override: dict[tuple[str, str], object] = {}   # (method, path) -> FakeResponse or exception

    def _answer(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        path = url.removeprefix(API)
        if (method, path) in self.override:
            answer = self.override[(method, path)]
            if isinstance(answer, Exception):
                raise answer
            return answer
        if method == "get" and path == "/articles/me/all":
            return FakeResponse(200, self.mine)
        if method == "get" and path == "/users/me":
            return FakeResponse(200, {"username": "rogue0015"})
        if method == "post" and path == "/articles":
            return FakeResponse(201, {"id": 11, "url": "https://dev.to/rogue0015/one-1a2b"})
        if method == "put":
            return FakeResponse(200, {"id": int(path.rsplit("/", 1)[1]), "url": "https://dev.to/rogue0015/one"})
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kwargs):
        return self._answer("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._answer("post", url, **kwargs)

    def put(self, url, **kwargs):
        return self._answer("put", url, **kwargs)

    def sent(self, method: str) -> list[dict]:
        return [kw["json"]["article"] for m, _url, kw in self.calls if m == method]


# ---- blog post -> Markdown ------------------------------------------------------------------

def test_post_body_becomes_markdown_with_absolute_links():
    assert post_body_markdown(PAGE, PAGE_URL) == EXPECTED


@pytest.mark.parametrize("html, markdown", [
    ('<pre><code class="language-python">x = 1\nprint(x)</code></pre>', "```python\nx = 1\nprint(x)\n```\n"),
    ("<p>Run <code>ls -la</code> now</p>", "Run `ls -la` now\n"),
    ("<p><strong>Bold </strong>text</p>", "**Bold** text\n"),
    ("<p># not a heading</p><p>1. not a list</p>", "\\# not a heading\n\n1\\. not a list\n"),
    ('<p><img src="../assets/a.png" alt="A chart"></p>', "![A chart](https://blog.test/assets/a.png)\n"),
    ("<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>", "- a\n  - b\n- c\n"),
    ("<p>a<br>b</p>", "a  \nb\n"),
    ("<h3>Small <em>heading</em></h3>", "### Small *heading*\n"),
])
def test_html_to_markdown(html, markdown):
    assert html_to_markdown(html, PAGE_URL) == markdown


def test_a_page_without_post_text_has_no_markdown():
    assert post_body_markdown("<html><body><p>Menu</p></body></html>", PAGE_URL) is None


def test_reader_returns_the_blog_post_as_an_article(app):
    article = ArticleReader(app.state.posts.db, fetch=lambda url, **kw: Page(PAGE)).read(PAGE_URL)
    assert (article.title, article.description, article.canonical_url, article.tags) == (
        "One", "Summary of one.", PAGE_URL, ("tech-community", "ai"))
    assert article.markdown == EXPECTED
    assert "FOOTER" not in article.markdown and "not content" not in article.markdown


def test_reader_explains_an_unreachable_or_empty_page(app):
    def down(url, **kw):
        raise requests.ConnectionError("no")
    with pytest.raises(ArticleError, match="Couldn't read the blog post https://blog.test/blogs/one.html"):
        ArticleReader(app.state.posts.db, fetch=down).read(PAGE_URL)
    with pytest.raises(ArticleError, match="Couldn't find the article text"):
        ArticleReader(app.state.posts.db, fetch=lambda url, **kw: Page("<html></html>")).read(PAGE_URL)


# ---- the adapter ---------------------------------------------------------------------------------

def test_tags_follow_devto_rules():
    assert devto_tags(["tech-community", "AI", "ai", "personal-branding", "speaking", "africa"]) == \
        "techcommunity, ai, personalbranding, speaking"


def test_liquid_in_the_text_is_kept_literal():
    assert escape_liquid("Hello {{ name }}") == "{% raw %}\nHello {{ name }}\n{% endraw %}"
    assert escape_liquid("Plain") == "Plain"


def test_publish_creates_an_article_with_the_blog_as_the_original():
    http = FakeDevto()
    result = DevtoPublisher(KEY, http=http).publish("share text is ignored", PostExtras(article=ARTICLE))
    assert http.sent("post") == [{
        "title": "One", "body_markdown": "{% raw %}\nHello {{ name }}\n\n{% endraw %}", "description": "Summary",
        "canonical_url": PAGE_URL, "tags": "techcommunity, ai", "published": True,
    }]
    assert http.calls[-1][2]["headers"]["api-key"] == "test-key"
    assert (result.external_id, result.external_url, result.warning) == ("11", "https://dev.to/rogue0015/one-1a2b", None)


def test_a_draft_is_saved_unpublished_and_says_so():
    http = FakeDevto()
    result = DevtoPublisher(KEY, http=http).publish("", PostExtras(article=ARTICLE, draft=True))
    assert http.sent("post")[0]["published"] is False
    assert result.warning == "Saved as a draft on dev.to. Publish it there when you're ready."


def test_sending_again_updates_the_existing_article():
    http = FakeDevto(mine=[{"id": 5, "canonical_url": PAGE_URL, "published": False}])
    result = DevtoPublisher(KEY, http=http).publish("", PostExtras(article=ARTICLE))
    assert http.sent("post") == [] and http.sent("put")[0]["published"] is True
    assert http.calls[-1][1] == f"{API}/articles/5"
    assert result.external_id == "5" and result.warning.startswith("Updated the article you already had")


def test_a_draft_never_takes_a_live_article_down():
    http = FakeDevto(mine=[{"id": 5, "canonical_url": PAGE_URL + "/", "published": True}])
    result = DevtoPublisher(KEY, http=http).publish("", PostExtras(article=ARTICLE, draft=True))
    assert "published" not in http.sent("put")[0]
    assert result.warning.endswith("It was already live, so it stays published.")


def test_a_text_only_post_is_refused():
    with pytest.raises(PublishError, match="publishes whole blog posts"):
        DevtoPublisher(KEY, http=FakeDevto()).publish("Hello")
    assert NOT_AN_ARTICLE.startswith("dev.to publishes whole blog posts")


@pytest.mark.parametrize("where, answer, message", [
    (("get", "/articles/me/all"), FakeResponse(401, {"error": "unauthorized", "status": 401}),
     "dev.to didn't accept the API key"),
    (("post", "/articles"), FakeResponse(422, {"error": "Title can't be blank", "status": 422}),
     "dev.to didn't accept the article: Title can't be blank"),
    (("post", "/articles"), FakeResponse(429, {"error": "Rate limit reached", "status": 429}),
     "dev.to is limiting how fast articles can be sent"),
    (("post", "/articles"), requests.ConnectionError("down"), "Couldn't reach dev.to (ConnectionError)"),
])
def test_errors_read_as_plain_sentences(where, answer, message):
    http = FakeDevto()
    http.override[where] = answer
    with pytest.raises(PublishError, match=message.replace("(", r"\(").replace(")", r"\)")):
        DevtoPublisher(KEY, http=http).publish("", PostExtras(article=ARTICLE))


def test_check_names_the_account_or_says_what_is_wrong():
    status = DevtoPublisher(KEY, http=FakeDevto()).check()
    assert (status.connected, status.can_post, status.account) == (True, True, "@rogue0015")
    http = FakeDevto()
    http.override[("get", "/users/me")] = FakeResponse(401, {"error": "unauthorized"})
    status = DevtoPublisher(KEY, http=http).check()
    assert not status.connected and status.message.startswith("dev.to didn't accept the API key")


def test_stats_come_from_one_read_of_the_article_list():
    http = FakeDevto(mine=[
        {"id": 11, "page_views_count": 120, "public_reactions_count": 9, "comments_count": 2},
        {"id": 12, "page_views_count": None, "public_reactions_count": 0, "comments_count": 0},
    ])
    pub = DevtoPublisher(KEY, http=http)
    assert pub.stats_enabled()
    assert pub.metrics("11") == {"impressions": 120, "reactions": 9, "comments": 2}
    assert pub.metrics("12") == {"reactions": 0, "comments": 0}
    assert len(http.calls) == 1
    with pytest.raises(PublishError, match="isn't on your dev.to account"):
        pub.metrics("99")


def test_the_adapter_needs_the_api_key():
    assert build_publisher("devto", {}) is None
    assert isinstance(build_publisher("devto", KEY), DevtoPublisher)


def test_connector_card_lists_the_key_by_name_only():
    card = connector_status(PLATFORMS["devto"], {}, False, NOW)
    assert card["state"] == "needs_keys" and card["checklist"] == [
        {"label": "API key", "key": "DEVTO_API_KEY", "done": False}]
    assert connector_status(PLATFORMS["devto"], KEY, True, NOW)["state"] == "connected"


# ---- scheduling through the app -----------------------------------------------------------------

@pytest.fixture
def fake_dev() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def dev_app(tmp_path, app, fake_x, fake_dev):
    settings = replace(app.state.settings, db_path=tmp_path / "dev" / "test.db")
    return create_app(settings, publishers={"x": fake_x, "devto": fake_dev})


@pytest.fixture
def c(dev_app):
    with TestClient(dev_app) as client:
        yield client


def blog_post(c, **overrides):
    payload = {"body": f"One\n\nSummary\n\n{PAGE_URL}", "scheduled_at": future(), "platforms": ["x", "devto"],
               "blog_url": PAGE_URL} | overrides
    return c.post("/api/posts", json=payload)


def test_devto_only_takes_blog_posts(c):
    r = blog_post(c, blog_url=None)
    assert r.status_code == 422
    assert r.json()["detail"] == ("dev.to publishes whole blog posts, so it only works with “From my blog”. "
                                  "Pick a blog post, or untick dev.to.")


def test_only_devto_can_save_a_draft(c):
    r = blog_post(c, options={"x": {"draft": True}})
    assert r.status_code == 422 and r.json()["detail"] == "X (Twitter) can't save a draft from the app."


def test_a_due_blog_post_goes_to_devto_as_the_whole_article(dev_app, c, fake_x, fake_dev):
    _seed(dev_app)
    post = blog_post(c, options={"devto": {"draft": True}}).json()
    assert next(t for t in post["targets"] if t["platform"] == "devto")["options"]["draft"] is True
    force_due(dev_app, post["id"])
    attachments = Attachments(dev_app.state.posts.db, dev_app.state.media, fetch=lambda url, **kw: Page(PAGE))
    assert publish_due(dev_app.state.posts, dev_app.state.publishers, attachments) == 2

    sent = fake_dev.extras[0]
    assert sent.draft is True and sent.article.title == "One" and sent.article.markdown == EXPECTED
    assert fake_x.sent == [f"One\n\nSummary\n\n{PAGE_URL}"] and fake_x.extras[0].article is None
    assert {t["status"] for t in c.get(f"/api/posts/{post['id']}").json()["targets"]} == {"published"}


def test_an_unreadable_blog_post_fails_with_the_reason(dev_app, c, fake_dev):
    post = blog_post(c, platforms=["devto"]).json()
    force_due(dev_app, post["id"])
    attachments = Attachments(dev_app.state.posts.db, dev_app.state.media, fetch=lambda url, **kw: Page("", 404))
    publish_due(dev_app.state.posts, dev_app.state.publishers, attachments)
    target = c.get(f"/api/posts/{post['id']}").json()["targets"][0]
    assert target["status"] == "failed" and target["error"].startswith(f"Couldn't read the blog post {PAGE_URL}")
    assert fake_dev.sent == []


def test_planning_blog_posts_for_devto(dev_app, c):
    _seed(dev_app)
    r = c.post("/api/blog/plan", json={"urls": [PAGE_URL], "start": future(24), "every_days": 2,
                                       "platforms": ["x", "devto"]})
    assert r.status_code == 201
    devto = next(t for t in r.json()[0]["targets"] if t["platform"] == "devto")
    assert devto["body_override"] is None   # it publishes the blog post, not share text


def test_preview_shows_what_devto_gets(dev_app, c):
    r = c.get("/api/blog/article", params={"url": PAGE_URL})
    assert r.status_code == 422 and r.json()["detail"].startswith("That post isn't in the blog library")
    _seed(dev_app)
    dev_app.state.articles.fetch = lambda url, **kw: Page(PAGE)
    article = c.get("/api/blog/article", params={"url": PAGE_URL}).json()
    assert article["title"] == "One" and article["tags"] == ["tech-community", "ai"]
    assert "## The context" in article["markdown"]


def test_devto_takes_no_test_post(c, fake_dev):
    r = c.post("/api/platforms/devto/test", json={"text": "Hello"})
    assert r.status_code == 422 and "can't take a test post" in r.json()["detail"]
    assert fake_dev.sent == []
