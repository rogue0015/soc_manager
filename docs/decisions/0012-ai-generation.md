# 0012: AI generation with Gemini: drafts per platform, personas, history, rate limits

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The owner wants an *AI generation* tab: start from an idea or a blog post, add an optional event
recap (event, day, venue, takeaways), pick platforms (or all) and a persona, and get a post
written for each platform. Agreed in discussion before building:

- Drafts are **reviewed**, then posted by hand (schedule or Post now). Nothing is posted by the AI.
- Personas are **interchangeable**: they hold tone, voice, language, format and so on, but never
  lengths or platforms. The platform registry decides the limits.
- The event recap is part of the post, not a separate mode.
- Every generation is **remembered**.
- Model: **Gemini 3.1 Flash-Lite** (`gemini-3.1-flash-lite`): free on Google's free tier,
  $0.25 / $1.50 per million input / output tokens on the paid tier. A first try with
  `gemini-3.8-flash` timed out after 100 s; Flash-Lite answers in a few seconds.
- **Rate limits** must be handled. Google applies them per project, resets the daily quota at
  midnight Pacific time, answers 429 when a limit is hit, and shows a project's exact numbers only
  in AI Studio.

## Options considered

- **Google's SDK** (`google-genai`) or **plain HTTPS** with `requests` (chosen): one endpoint
  (`generateContent`), no new dependency, and it can be faked in tests like the platform adapters.
- **Free-form text** or **JSON with a schema** (chosen): `responseSchema` returns one draft per
  platform as data, so the app can check each one.
- **Rate limits only by retrying 429s**, or **pacing ourselves too** (chosen): staying under the
  budget avoids most refusals; the 429 handling covers what's left (other apps on the same project,
  a wrong budget).

## Decision

- `services/gemini.py`: the client. Key `GEMINI_API_KEY`; model `GEMINI_MODEL` (default
  `gemini-3.1-flash-lite`); budget `GEMINI_RPM` / `GEMINI_RPD` (defaults 15 and 500: check your
  project at aistudio.google.com/rate-limit). On 429 it reads Google's `RetryInfo.retryDelay`: up to
  15 s is waited out once and retried; longer, or a per-day quota, becomes a sentence saying when
  to try again.
- `services/ai_writer.py`: builds the system instructions (rules + persona) and the prompt (each
  platform's limit and style hint, the idea or the blog post's full text, the event recap), checks
  every draft against its platform's limit (sent back once to be shortened, then flagged), asks again
  for a missing title (YouTube), and flags links the author didn't give. `Usage` keeps requests
  under the budget: per minute in memory (waits up to 10 s), per day in the `ai_usage` table by
  Pacific day, so a restart doesn't reset it.
- `services/personas.py`: personas in the `personas` table, two starter personas on request.
  Generations in the `generations` table (newest 200), each with a copy of its persona.
- `routes/ai.py` and the *AI generation* tab (`js/ai.js`, `js/personas.js`): **Open in post window**
  fills the normal post window for that platform; saving it records which draft became a post.
- dev.to is left out: it publishes the whole blog post, so there's nothing to write for it.

## Consequences

- Ideas and blog text are sent to Google. On the free tier Google may use them to improve its
  products (its pricing page says so); the privacy policy page says this.
- The prompt says to use only the facts given, but a model can still get things wrong: review is
  required by design, and made-up links are flagged.
- Each generation costs one request, two if a draft needs fixing; Regenerate costs one more.
- If the real limits differ from `GEMINI_RPM` / `GEMINI_RPD`, the 429 handling still explains what
  happened; setting the right numbers makes the app wait or refuse before Google does.
