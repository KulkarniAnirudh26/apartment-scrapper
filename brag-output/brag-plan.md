# Brag Plan: Apartment 1B1B Rent Scraper

## What is this app?
A scheduled Python scraper that crawls 174 Bay Area apartment sites twice a day, logs every 1-bed/1-bath rent to an append-only history, ranks the best $/sq ft inside a filter band, and emails its owner a shortlist. The funny part is how much industrial machinery is pointed at renting one bedroom.

## The angle
A serious proptech launch for an operation whose entire market is one person's next lease. Every number is real: 174 sites, about 40s each, 1h55m per run, 08:30 and 20:30. The narration stays dry and never winks. The joke only comes out at the end: "Two hours of scraping. One bedroom."

## Hook (first 2-3 seconds)
A dark terminal. `$ python scraper.py` types out, then the real log floods in (`[2/174] … method=playwright, status=ok, 41s`) while a `[n/174]` counter races. On the first strong beat (3.70s) a deadpan caption slams in: **"Target: one (1) bedroom."**

## Key moments (the middle)
- The three-stage pipeline as cards: HTTP (0 tokens) → Playwright (0 tokens) → Claude, labelled "the only path that spends tokens".
- The explorer scatter: a cursor drags **Rent ceiling** from $4,400 down to $3,900. Dots outside the band dim, and on a beat the picks ① Estancia, ② Encasa and ③ Birch Creek pop.
- Encasa 1x1a's rent history draws $3,831 → $3,711 (−$120), next to Birch Creek's +$459.

## Outro / punchline
"Apartment 1B1B Rent Scraper", then "174 sites. 2 hours. 1 bedroom." The narration says: "Two hours of scraping. One bedroom."

## User flow worth showing
The scheduled run scrapes (log ticking) → the explorer filters and ranks by $/sq ft → the "Top 3 picks" email lands at 08:30.

## Tone
- Preset: yc-parody
- Creative direction: serious proptech launch for a market of one lease
- Interpretation: hard cuts, one claim per scene, sentence case, data set in monospace, sparse restrained SFX. The product's absurd diligence is the joke.

## Format: landscape — 1920x1080
## Duration: 24.4s, set by the voiceover

## Visual identity (from plots/explorer.html)
- Background: #fcfcfb (light scenes); #1a1a19 (terminal scene, the explorer's dark surface)
- Accent: #2a78d6 (dark: #3987e5); city colours #2a78d6, #eb6834, #1baf7a, plus a 4th
- Text: #0b0b0b; muted #52514e
- Display font: system UI stack (Segoe UI in render), semibold, tight tracking
- Body font: same; Cascadia Mono / Consolas for log and data
- Strongest visual element: the rent-vs-size scatter with the dashed filter band and numbered picks

## Share copy (draft)
I built a 174-site scraper that runs twice a day, renders JavaScript and emails me a ranked shortlist. All to rent one 1-bedroom.

## Audio direction
- Role: warm bed under narration, sparse professional accents
- Music: happy-beats-business-moves-vol-11 (114.8 BPM, warm, business-like)
- Music treatment: 0.35 at the open, ducked to about 0.13 under the voiceover, back up briefly after the last line, fade out over the final 0.8s
- Music cue guidance: preset `assets/music/cues/happy-beats-business-moves-vol-11-by-ende-dot-app.music-cues.json`. Strong cues used:
  - 3.70s: hook caption
  - 12.65s: picks reveal
  - 22.65s: outro accent

  Beat grid for the pipeline cards: 5.80 / 6.86 / 7.91, every other beat so each card can be read.
- Audio-reactive treatment: subtle, if extraction is available. Otherwise none.
- SFX posture: sparse
  - keypresses on the typed command
  - one card-place per pipeline card
  - a mouse click on the slider grab
  - a soft drop on the picks
  - a card slide on the email
  - one soft impact on the outro
- Restraint rule: nothing loud or bright, and no SFX over narration peaks beyond soft accents

## Voiceover script
Kokoro `af_heart` at 1.1×, one clip per scene; the scene lengths flex to the clips:
1. "Finding a one-bedroom in the Bay Area is a full-time job. So I automated it." (4.67s)
2. "It crawls a hundred seventy-four buildings, and brings a real browser when sites fight back." (4.91s)
3. "Then it plots rent against size, and ranks every deal by price per square foot." (4.25s)
4. "Every run adds to the history, so I know the moment rent drops." (3.39s)
5. "Twice a day, the shortlist is in my inbox." (2.37s)
6. "Two hours of scraping. One bedroom." (2.09s)

## Storyboard

### Scene 1 — The log — 0.0–5.1s
Dark terminal. `$ python scraper.py` types out, then the real-format log lines scroll with a `[n/174]` status counter. The final line reads "Scraped 174 site(s) in 115 min (40s each)". At 3.70s the caption "Target: one (1) bedroom." slams in and holds.
- Sequential/interaction: typed command; log lines stream as texture (not must-read); the caption holds 1.4s.
- Audio intent: crisp start, the machine at work.
- Audio-coupled idea: keypress ticks on typing; soft drop on the caption.
- Transition mood: hard → Scene 2

### Scene 2 — The pipeline — 5.1–10.2s
Header "Per site, in order. Stops as soon as it's confident." Three cards arrive left to right with arrows:
- HTTP fetch: JSON-LD, `__NEXT_DATA__`, DOM · **0 tokens**
- Playwright: headless Chromium for client-side pricing · **0 tokens**
- Claude: only with `--use-llm`, only when still unsure · **the only path that spends tokens**

All three stay on screen together.
- Sequential/interaction: cards on beats 5.80, 6.86 and 7.91, then held together.
- Audio-coupled idea: card-place on each card.
- Transition mood: hard → Scene 3

### Scene 3 — Explorer — 10.2–14.7s
Recreated `explorer.html`: the "1B1B rent vs. size" title, a controls panel (Minimum size 750 sq ft, Rent floor $3,200, Rent ceiling slider), and a scatter of the 24 real floor plans coloured by city, with a median $/sq ft line. The filter band appears. A cursor drags the ceiling $4,400 → $3,900, and dots outside the band dim. At 12.65s the picks ①②③ pop on the chart and in a Picks list: Estancia $4.38, Encasa $4.58, Birch Creek $4.54/sq ft.
- Sequential/interaction: simulated slider drag; the picks list rows reveal quickly and then hold for 2s.
- Audio-coupled idea: mouse click on the grab; soft drop on the picks.
- Transition mood: hard → Scene 4

### Scene 4 — Rent history — 14.7–18.3s
Encasa · 1x1a · 811 sq ft. The line draws Aug 29 → Sep 13, $3,831 → $3,711, and a green "−$120" badge lands. Birch Creek's faint orange line is labelled "+$459". A chip reads `rent_history.csv · append-only`.
- Audio-coupled idea: none (let the narration carry it).
- Transition mood: hard → Scene 5

### Scene 5 — Inbox — 18.3–20.9s
An email card slides up. "RentScrape · 08:30 · to me". Subject "Top 3 picks". Three rows from `plots/recommendations.md`.
- Audio-coupled idea: card slide on arrival.
- Transition mood: hard → Scene 6

### Scene 6 — Outro — 20.9–24.4s
"Apartment 1B1B Rent Scraper" lands on beat 21.07. "174 sites. 2 hours. 1 bedroom." follows at 21.59. At 22.65 "1 bedroom." is underlined in accent blue, with a soft impact. The music fades out.

**Music mood for this video:** parody-corporate, warm
**Audio summary:** a warm business bed ducked under dry narration, with a few tactile UI accents and a soft impact on the punchline.
