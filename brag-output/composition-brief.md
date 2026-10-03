# Hyperframes Composition Brief: Apartment 1B1B Rent Scraper

## Objective
Create a short launch-style brag video, with narration, for the Apartment 1B1B Rent Scraper.

## Output
- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape, 1920x1080
- Duration: 24.4s (set by the voiceover)

## Source Material
- Project root: `C:\Users\aniru\Personal\apartment-scraper`
- Primary files read: `README.md`, `scraper.py`, `plots/explorer.html`, `plots/recommendations.md`, `plots/email-preview.html`
- Product name: Apartment 1B1B Rent Scraper
- Strongest claim: 174 sites at about 40s each, 1h55m per run, twice a day (08:30 / 20:30)
- Key UI to recreate: the explorer scatter with the filter band, slider and numbered picks; the log format from `scraper.py`; the "Top 3 picks" email
- Data: `composition/data.js` (the real points and history, extracted from `explorer.html`)
- Copy that must appear verbatim:
  - `method=playwright, status=ok`
  - "1B1B rent vs. size"
  - "the only path that spends tokens"
  - "Top 3 picks"

## Creative Direction
- Tone preset: yc-parody
- Creative direction: serious proptech launch for a market of one lease
- Interpretation: hard cuts, one claim per scene, data set in monospace, restrained audio
- Hook: the terminal log floods, then "Target: one (1) bedroom."
- Outro / punchline: "174 sites. 2 hours. 1 bedroom."
- Avoid: generic SaaS language, abstract filler, restyling the product UI

## Visual Identity
- Background: #fcfcfb / terminal #1a1a19
- Text: #0b0b0b / #52514e muted (light); #ffffff / #c3c2b7 (dark)
- Accent: #2a78d6 (light) / #3987e5 (dark)
- Fonts: system UI stack; Cascadia Mono / Consolas for data

## Storyboard
See `brag-plan.md`. Scenes:
1. Log (0–5.1)
2. Pipeline (5.1–10.2)
3. Explorer (10.2–14.7)
4. History (14.7–18.3)
5. Inbox (18.3–20.9)
6. Outro (20.9–24.4)

## Audio
- Voiceover: six Kokoro `af_heart` clips in `assets/vo/`, starting at 0.30, 5.15, 10.25, 14.75, 18.35 and 21.00s
- Music: vol-11, pre-mixed to `assets/music/bed.wav` with ffmpeg. The ducking envelope is baked in: 0.35 → 0.13 under the voiceover → fade out. Placed at data-volume 1.
- Cue locks:
  - 3.70s: caption
  - 12.65s: picks
  - 22.65s: outro accent
  - Beat grid for the cards: 5.80 / 6.86 / 7.91
- Audio-reactive: skipped. The Hyperframes creative skill's extraction helper isn't installed in this environment.
- SFX: keypress, card-place ×3, mouseclick, drop, card-slide, impactSoft_medium_001. All at 0.5–0.7.

## Hyperframes Instructions
Follow the `npx hyperframes docs` contract:
- `data-composition-id`
- `.clip` elements with `data-start` / `data-duration`
- a paused GSAP timeline registered on `window.__timelines`
- deterministic logic only

Run `npx hyperframes check` before render.
