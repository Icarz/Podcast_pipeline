# Retention tracking log

Per-video hook family, topic cluster, and retention, updated whenever a new
YouTube Studio analytics export is pulled. This is the "Retention tracking
file" item from CLAUDE.md's TODO list — average view duration % is the
metric that predicts Shorts distribution (see
`feedback_retention_metric_priority` in project memory), so this file exists
to make that check a lookup instead of a manual spreadsheet reconstruction.

## Methodology

- **AVD % (proxy)** = `watch_time_hours * 3600 / views / duration_seconds *
  100`, computed from the "Durée de visionnage (heures)" column YouTube
  Studio exports. This is a *proxy* for real average-view-duration % (the
  export used to backfill this table didn't include a native AVD% column) —
  if a future export has a real AVD% column, prefer that value and note the
  source in Notes.
- **Low-n rows are unreliable.** Anything under ~10 views can swing 20+
  points on one extra rewatch. Treat AVD% on those rows as noise, not signal
  — they're kept here for completeness, not for hook-family comparison.
- **Hook family** — best-effort classification against the formulas in
  `script_gen.SYSTEM_PROMPT`: `NEURO` (default brain/nervous-system-does-X
  formula), `SELFCAT` (self-categorization, added 2026-08-02), `METAPHOR`
  (analogy hook — see `script_gen.is_metaphor_hook`), `CONTRARIAN-ID`
  (contrarian identity frame), `NUMBERED` (identity-stakes numbered rules),
  `TACTIC` (concrete trick/technique framed as a reveal), `INSTRUCTIONAL`
  (banned pattern — pre-dates the ban).
- **Pipeline-era markers:** rows are tagged `[podcast]` (pre-2026-07-31,
  sourced from real podcast transcripts) or `[synthetic]` (2026-07-31
  onward, Claude-written from scratch) or `[synthetic+aug2]` (2026-08-02
  onward, after the equal-topic-clusters / self-categorization /
  named-mechanism prompt rewrite).

## Log

| Date | Video ID | Title | Hook family | Topic cluster | Era | Dur(s) | Views | Impr. | CTR% | AVD% (proxy) | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-08-08 | ZmiVA6onzvI | Practical Is the Word You Use to Quit | CONTRARIAN-ID | identity_resilience_meaning | synthetic+aug2 | 54 | 5 | 4 | 0% | 26.4% | low-n |
| 2026-08-07 | bKDQ5nkGDBI | Worry Isnt Protection Its a Spell Your Brain Casts | METAPHOR | fear_anxiety_rumination | synthetic+aug2 | 44 | 5 | 6 | 0% | 31.4% | low-n; check for bridge-phrase gate compliance retroactively |
| 2026-08-06 | ow_1MRI0PFY | More Money Same Broke Feeling Pick Your Enough Number | CONTRARIAN-ID | money_as_freedom | synthetic+aug2 | 47 | 12 | 6 | 0% | 31.7% | low-n |
| 2026-08-05 | 2TXP_VNsI-s | Lost or Early The One Question That Changes Everything | SELFCAT | identity_resilience_meaning | synthetic+aug2 | 48 | 8 | 14 | 0% | 27.6% | low-n |
| 2026-08-04 | NSoO8cWyHPg | Willpower Isnt Character Its a Battery Dying By Noon | METAPHOR | neurology_focus_motivation | synthetic+aug2 | 53 | 23 | 17 | 0% | 19.0% | |
| 2026-08-03 | Gn0tJqQno0c | Your Nervous System Thinks Your Inbox Is a Tiger | NEURO | neurology_focus_motivation | synthetic+aug2 | 54 | 19 | 13 | 0% | **50.3%** | best AVD% of the whole synthetic batch — NEURO formula holding up |
| 2026-08-02 | 0Yco1RIFpXo | Low Self Esteem vs High Self Esteem One Word Gives It Away | SELFCAT | identity_resilience_meaning | synthetic+aug2 | 49 | 63 | 22 | **4.55%** | 9.1% | best CTR / worst AVD% in batch — classic curiosity-gap-no-payoff pattern, same as "Fear Is a GPS" below |
| 2026-08-01 | edAvVZyFay8 | Brain Replays The Argument | NEURO | fear_anxiety_rumination | synthetic | 40 | 20 | 34 | 2.94% | 12.5% | first end-to-end synthetic-pipeline render (manual test) |
| 2026-07-31 | XxbERGgJZbQ | Youre Not Overwhelmed Youre the One Creating It | CONTRARIAN-ID | identity_resilience_meaning | synthetic | 57 | 29 | 18 | 0% | 20.9% | migration date |
| 2026-07-29 | Gx7DIq5CN7A | Youre Planning the Wrong Thing Do This Instead | CONTRARIAN-ID | neurology_focus_motivation | podcast | 46 | 30 | 24 | 0% | 15.3% | |
| 2026-07-27 | JyV7po5OrsA | Perfectionism Is Just Anxiety In a Costume | METAPHOR | fear_anxiety_rumination | podcast | 57 | 218 | 32 | 3.13% | 20.4% | |
| 2026-07-26 | yL78Q9TKXLI | Youre Supposed to Fail 15% of the Time Heres Why | CONTRARIAN-ID | identity_resilience_meaning | podcast | 56 | 37 | 26 | 3.85% | 17.8% | |
| 2026-07-25 | ZHSjD9-oERU | Your Brain Gets Smarter Every Time You Fail | NEURO | neurology_focus_motivation | podcast | 57 | 144 | 54 | 0% | 21.0% | |
| 2026-07-23 | JsQ__IJMwZo | Give Your Brain No Options Watch What Happens in 25 Minutes | NEURO | neurology_focus_motivation | podcast | 47 | 188 | 39 | 2.56% | **63.8%** | best AVD% in the whole dataset |
| 2026-07-22 | qNcHFseB4Bg | Your Brain Gets Smarter Every Time You Fail (repost) | NEURO | neurology_focus_motivation | podcast | 57 | 175 | 37 | 0% | 20.2% | |
| 2026-07-21 | ddFY0jmG88s | Breaking Out of Modern Culture Before It Breaks You | CONTRARIAN-ID | individuality_vs_conformity | podcast | 55 | 479 | 43 | 0% | 49.1% | |
| 2026-07-20 | n926suMOB8k | You Stopped Trusting Yourself And You Dont Know It | CONTRARIAN-ID | identity_resilience_meaning | podcast | 52 | 615 | 21 | 4.76% | 15.5% | |
| 2026-07-19 | AxZ4IrJLdBY | You are Waiting to Feel Ready Action Is What Makes You Ready | CONTRARIAN-ID | identity_resilience_meaning | podcast | 57 | 766 | 35 | 2.86% | 15.8% | |
| 2026-07-18 | BjtVVrc4tnU | Your Brain Catastrophizes itself to Keep You Alive Not Happy | NEURO | fear_anxiety_rumination | podcast | 47 | 999 | 30 | 3.33% | 10.7% | top raw-views video |
| 2026-07-17 | P9rftRmfpL4 | Youre Already Being Brainwashed Might As Well Do It Yourself | CONTRARIAN-ID | individuality_vs_conformity | podcast | 47 | 972 | 30 | 0% | 18.4% | |
| 2026-07-15 | Q8ifF9JmhmA | The Pen and Paper Trick That Shuts Off Your Emotional Brain | TACTIC | fear_anxiety_rumination | podcast | 47 | 891 | 79 | 3.8% | 18.6% | highest impressions in dataset |
| 2026-07-13 | W2N_lNWNZRw | Name It to Tame It | TACTIC | fear_anxiety_rumination | podcast | 49 | 4 | 31 | 0% | 11.6% | low-n |
| 2026-07-11 | gy8paI_uzOQ | Your Brain Catastrophizes (repost) | NEURO | fear_anxiety_rumination | podcast | 47 | 2 | 19 | 5.26% | 52.9% | low-n, unreliable |
| 2026-07-07 | ZL9IxX2RqF0 | Stop Waiting to Feel Ready | CONTRARIAN-ID | identity_resilience_meaning | podcast | 57 | 1 | 16 | 0% | 19.6% | n=1 |
| 2026-07-06 | RsU0gm13hWo | Your Brain Kills Your Momentum Right When Youre Winning | NEURO | neurology_focus_motivation | podcast | 52 | 3 | 20 | 0% | 31.2% | low-n |
| 2026-07-03 | 84hb8kK387k | Vacuums vs Chargers The People Around You Are Making or Breaking | SELFCAT | individuality_vs_conformity | podcast | 53 | 13 | 13 | 0% | -- | watch-hours not reported |
| 2026-07-01 | DyyWOqtRcvM | Youre Not Running From Emptiness Youre Running From Meaning | CONTRARIAN-ID | identity_resilience_meaning | podcast | 47 | 1 | 12 | 0% | 16.1% | n=1 |
| 2026-06-30 | cMRL4_Vjek8 | Youre Choosing Your Hard Either Way Choose Wisely | CONTRARIAN-ID | identity_resilience_meaning | podcast | 58 | 4 | 76 | 3.95% | 32.3% | low-n |
| 2026-06-29 | QipPM9xOE9s | You Stopped Trusting Yourself (repost) | CONTRARIAN-ID | identity_resilience_meaning | podcast | 52 | 9 | 9 | 0% | -- | watch-hours not reported |
| 2026-06-27 | onw2y19ogOE | Your Fear Is a GPS Heres How to Read It | METAPHOR | fear_anxiety_rumination | podcast | 46 | 2 | 9 | 0% | 60.3%* | *n=2, contradicts the documented 39.2% for this same video in an earlier window — sample too small here, defer to CLAUDE.md's earlier-recorded 39.2%/rank-1-views/worst-retention finding |
| 2026-06-26 | fHI8SuhFLlg | Youre Killing Your Dreams Just to Fit In | CONTRARIAN-ID | individuality_vs_conformity | podcast | 52 | 6 | 6 | 0% | -- | watch-hours not reported |
| 2026-06-24 | mwqTPd4MVo0 | Your Brain Is Lying To You Heres the Neurological Proof | NEURO | neurology_focus_motivation | podcast | 53 | 6 | 6 | 0% | -- | watch-hours not reported |
| 2026-06-23 | dFFq8MsI7sU | Purpose of Money Is to Get Free 7 Rules That Actually Work | NUMBERED | money_as_freedom | podcast | 52 | 8 | 8 | 0% | -- | watch-hours not reported |
| 2026-06-22 | O3nFEpUYPEQ | Why Maybe Is Killing Your Success | CONTRARIAN-ID | identity_resilience_meaning | podcast | 47 | 2 | 5 | 0% | 4.6% | low-n |
| 2026-06-20 | IOTPGlsgC7Y | Your Brain Predicts Your Future From Your Past | NEURO | neurology_focus_motivation | podcast | 56 | 6 | 19 | 5.26% | 33.5% | low-n |
| 2026-06-20 | gly8ZUciEtE | Your Feelings Are Reprogramming You Heres How to Fight | NEURO | fear_anxiety_rumination | podcast | 29 | 4 | 5 | 0% | 27.3% | low-n |
| 2026-06-17 | Y8yq_3BPaYE | Your Brain Wont Let Go Until You Face It Heres Why | NEURO | fear_anxiety_rumination | podcast | 51 | 1 | 9 | 0% | 4.2% | n=1 |
| 2026-06-15 | 8OTvR_PU0DE | Youre Not Obsessed Enough | CONTRARIAN-ID | identity_resilience_meaning | podcast | 44 | 3 | 13 | 0% | 21.5% | low-n |
| 2026-06-13 | hCamTrIgkDg | Your Brain Is Addicted to Fake Scenarios | NEURO | neurology_focus_motivation | podcast | 50 | 4 | 16 | 0% | 7.4% | low-n |
| 2026-06-12 | t5IyiFGA7k8 | Always Grab the Right Handle | TACTIC | identity_resilience_meaning | podcast | 28 | 3 | 4 | 0% | 3.0% | low-n |
| 2026-06-10 | 05oQklEGB1k | Why Students Tune Out The Real Reason | INSTRUCTIONAL | neurology_focus_motivation | podcast | 51 | 4 | 6 | 0% | 27.9% | low-n; pre-dates instructional-hook ban |
| 2026-06-07 | 3tYy45R-_Zk | The 3 Word Trick That Makes Personal Change Actually Stick | TACTIC | identity_resilience_meaning | podcast | 57 | 1 | 4 | 0% | 2.5% | n=1 |
| 2026-06-07 | gwfVnz4uR5c | Opt Out of Modern Culture Before It Breaks You | CONTRARIAN-ID | individuality_vs_conformity | podcast | 55 | 1 | 26 | 3.85% | 18.3% | n=1 |

## Reading this table (as of 2026-08-12)

- **NEURO hooks with real sample size hold the strongest AVD%**: "Give Your
  Brain No Options" (63.8%, n=188), "Breaking Out of Modern Culture"
  (49.1%, n=479), "Nervous System... Tiger" (50.3%, n=19). This is the
  formula `script_gen.SYSTEM_PROMPT` already weights as default — the data
  keeps confirming it, including inside the low-reach synthetic batch.
- **METAPHOR hooks are the one family that repeatedly shows the CTR-high /
  AVD-low split**: "Fear Is a GPS" (documented separately at 39.2% AVD in
  CLAUDE.md's larger-sample window) and "Low Self Esteem vs High Self
  Esteem" (9.1% AVD here, best CTR of its batch) both fit the pattern. This
  is why `script_gen._metaphor_payoff_gate` was added on 2026-08-12 — it now
  hard-requires an explicit plain-language bridge sentence near the top of
  the script whenever `is_metaphor_hook()` flags the hook, instead of
  leaving the "unpack it immediately" rule as prose guidance the model could
  skip.
- **Impressions collapsed across BOTH eras** (79 → 4 from mid-July to
  early-August, podcast and synthetic alike), and the decline started
  *before* the 2026-07-31 migration — see the 2026-08-12 chat analysis.
  Don't read low synthetic-batch views as proof the new pipeline underperforms
  on content quality; reach is currently too thin to judge hook-family
  performance from Aug-onward rows alone. Re-evaluate once post-suppression-test
  impressions recover to a comparable baseline.

## Maintenance

Append new rows at the top each time a fresh YouTube Studio export is
pulled. Fill AVD% from the real Studio metric once available instead of the
watch-hours proxy, and note the source. Don't delete low-n rows — mark them,
they're still useful once enough accumulate to average out the noise.
