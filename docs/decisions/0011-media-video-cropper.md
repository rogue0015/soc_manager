# 0011: Media library, video posting (YouTube, TikTok, Instagram, LinkedIn, Bluesky) and a picture cropper

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The owner asked for a media feature that reaches YouTube, Instagram and anywhere else
that takes Shorts and videos, and for a cropper so pictures go out in the best shape
for each platform. They approved, one by one: the cropper, a video library with video
posts on LinkedIn and Bluesky, YouTube, Instagram (built, inactive), TikTok, and a
Media tab. They declined video trimming and reshaping, because it would add ffmpeg
(~80 MB) to the image.

What each platform allows a personal app:
- **LinkedIn** (`w_member_social`) and **Bluesky** (app password): video works today.
  LinkedIn takes MP4, 3 s–30 min, ≤ 500 MB, uploaded in 4 MB parts. Bluesky takes ≤ 3 min,
  ≤ 100 MB, through its video service; pictures ≤ 1 MB.
- **YouTube**: Google OAuth with a refresh token. Uploads from projects Google hasn't
  audited are locked to private. In "Testing" mode, refresh tokens expire after 7 days.
  Shorts are vertical videos of up to 3 minutes; there is no separate API for them.
- **TikTok**: OAuth with 2-hour access tokens and a refresh token that is *replaced* on
  every use. Unaudited apps post privately only. The redirect URL must be https.
- **Instagram**: the Instagram API with Instagram Login needs a professional account and
  Meta's review, and its redirect URL must be https. Photos are fetched from a public URL,
  which this app doesn't have. Reels can be sent with a resumable upload instead.

## Options considered

1. **A picture-cropping library from a CDN** vs **a hand-built `<canvas>` cropper**: the
   project has no build step and keeps dependencies few, and the cropper needs only
   drag, zoom, fixed shapes and JPEG export. Hand-built it is.
2. **Reading video size and length with ffmpeg** (declined above) vs **reading the
   MP4/MOV boxes directly**: `mvhd` gives the length and the video track's `tkhd` the
   size and rotation. That is enough to warn about shape and length.
3. **Uploads in memory** (as for pictures) vs **streamed to a temporary file**: videos
   can be up to 1 GB, so they are streamed to disk. nginx stops buffering request bodies
   for `/api/` and allows 1100 MB.
4. **A picture per platform chosen in each platform's options** vs **one shared
   "Picture or video" section**: one section, with one crop per platform that takes
   pictures. LinkedIn keeps its own choice (nothing, picture/video, or link preview).

## Decision

- `media` gains `kind`, `name`, `width`, `height` and `duration_ms`; `post_metrics`
  gains `views`. Pictures can be up to 8 MB, videos up to 1 GB (MP4 or MOV), checked
  by their first bytes. `GET /api/media` lists the library with what uses each item;
  `DELETE` refuses while a scheduled post still needs it.
- Post options gain `attachment: "video"`, `video_id`, `title`, `privacy` and `tags`.
  Platforms with `video_only` (YouTube, TikTok, Instagram) refuse a post without a video.
- The registry carries each platform's `image_formats` (best first), `image_max_bytes`
  and video limits, so the cropper and the post dialog's warnings never hard-code them.
  The adapters check the limits again.
- New adapters `youtube.py`, `tiktok.py` and `instagram.py` sign in through the existing
  "Sign in with …" flow (ADR 0005). Rotated tokens are saved through
  `base.save_tokens`, which `main.py` points at the env file.
- The cropper renders each platform's crop at its exact size as JPEG, lowering quality
  (then size) until it fits that platform's limit. Crops are saved to the library as
  their own files.

## Consequences

- YouTube posts land as private until Google audits the project; the app says so on
  each post. TikTok posts are private until its audit. Instagram stays inactive until
  Meta's review is done and an https redirect URL is available (a tunnel or hosting).
- With the Google consent screen in Testing, the YouTube sign-in must be repeated weekly;
  publishing the consent screen avoids that.
- Videos can't be trimmed or reshaped in the app; the post dialog warns when a video
  won't fit a platform (too long, too large, too short).
- Animated GIFs become still pictures when cropped.
- Polling for processing (LinkedIn, Bluesky, TikTok, Instagram) can keep the scheduler
  busy for a few minutes per video post.
