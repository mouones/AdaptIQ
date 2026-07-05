# AdaptIQ — Pre-Product Study & Problem Analysis

> Part 1 of the AdaptIQ design dossier. This document explains *why* the product
> exists and the reasoning that shaped it, before any code. See
> [`docs/design/README.md`](./README.md) for the full index.

## 1. Problem statement

Traditional quiz and e-learning tools serve **the same questions at the same
difficulty to everyone**. Two failure modes follow:

- **Strong learners are bored** — questions are too easy, so they disengage and
  learn nothing new.
- **Struggling learners are overwhelmed** — questions are too hard, so they fail
  repeatedly, lose confidence, and quit.

Both are symptoms of a single missing capability: the system does not know *where
each learner is* and cannot *adapt* to it. Learning is most effective in the
**Zone of Proximal Development (ZPD)** — the band of difficulty that is
challenging but achievable (empirically, a success rate around **60–75%**).
A static quiz cannot target that band because the band is different for every
person and moves as they learn.

## 2. Motivation

AdaptIQ was built to answer one question:

> *Can a web app continuously estimate each learner's ability per topic and serve
> the next question at exactly the right difficulty — cheaply enough to run in
> real time?*

Getting this right requires three things working together:

1. A **measurement model** that turns a stream of right/wrong answers into a
   numeric ability estimate (this is what Item Response Theory provides).
2. A **content pipeline** that can supply enough fresh, correctly-difficult
   questions to keep the loop going (a fixed question bank is exhausted quickly).
3. **Engagement mechanics** (points, levels, ranks, 1-v-1 duels) so learners keep
   coming back long enough for the adaptation to matter.

## 3. Target users

- **Primary:** self-directed learners studying factual domains (the reference
  build ships History and Geography) who want practice calibrated to their level.
- **Secondary:** an **administrator/educator** who curates the question bank,
  approves community-suggested topics, and monitors quality and safety.

## 4. Domain choice: why History & Geography first

These domains were chosen deliberately for the initial build because they are:

- **Factual and verifiable** — a question has an unambiguous correct answer,
  which makes both automatic grading and automatic *quality checking* tractable.
- **Richly sourced** — Wikipedia, Wikidata, DBpedia, and open data (World Bank,
  UN) provide structured facts to ground LLM question generation and to
  fact-check it, reducing hallucination risk.
- **Naturally tiered** — "capital of France" vs. "smallest capital by population"
  are obviously different difficulties, giving the IRT model real signal.

The architecture is domain-agnostic; adding a domain is a data/config task, not a
rewrite.

## 5. Objectives (what success looks like)

| # | Objective | How it is realised |
|---|-----------|--------------------|
| O1 | Estimate each user's ability **per concept**, not globally | Per-concept IRT θ tracked in `user_concept_theta` |
| O2 | Serve questions inside each user's **ZPD** | ZPD band targeting in question selection |
| O3 | Never run out of correctly-difficult questions | LLM + RAG generation with a warm Redis queue |
| O4 | Keep generated content **trustworthy** | Multi-source grounding + a governance/validation layer |
| O5 | Sustain **engagement** | Points/levels, Challenge ranks (E→A), PvP ELO, streaks |
| O6 | Stay **fast** under real use | Pre-generation off the request path, Redis caching |
| O7 | Be **safe** and operable | Cookie+CSRF auth, admin controls, rate limits, audits |

## 6. Constraints & assumptions

- **Real-time budget:** the "next question" request must feel instant. Anything
  slow (LLM calls, multi-source fetches) must happen **ahead of time**, not on
  the request path. This single constraint drives most of the caching design.
- **Cost/limits:** the LLM provider is rate-limited and metered, so generation is
  batched, cached, de-duplicated, and backed off on `429`.
- **Trust:** LLM output is treated as *untrusted* until validated against
  structured facts — a hallucinated "fact" is worse than no question.
- **Single-region, modest scale** for the reference build; the design keeps state
  in PostgreSQL + Redis so the API can scale horizontally later.

## 7. Non-goals (explicitly out of scope for the reference build)

- Full multi-tenant / classroom-management features.
- Open-ended (essay) grading — the model is built around MCQ + short factual
  answers where automatic grading is reliable.
- A mobile native app (the frontend is a responsive web app).

---

Next: [02 — Requirements Analysis](./02-requirements.md).
