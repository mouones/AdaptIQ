# AdaptIQ — Design Dossier

The reasoning behind AdaptIQ: *why* it exists, *what* it must do, *how* it is
built, and *why* each major choice was made. Read in order for a full pre-product
→ design → engineering narrative, or jump to a topic.

| # | Document | What it covers |
|---|----------|----------------|
| 1 | [Pre-Product Study](./01-pre-product-study.md) | Problem, motivation, target users, domain choice, objectives, constraints, non-goals |
| 2 | [Requirements Analysis](./02-requirements.md) | Actors, functional & non-functional requirements, key trade-offs — mapped to code |
| 3 | [Architecture & Technology Choices](./03-architecture-and-technology-choices.md) | System shape, layering, and the rationale for FastAPI / PostgreSQL / Redis / Groq / RAG / React |
| 4 | [Learning Engine](./04-learning-engine.md) | The core: IRT (1PL), per-concept θ, ZPD selection, spaced repetition, points/levels, Challenge ranks, PvP ELO, content trust |
| 5 | [Quality & Performance](./05-quality-and-performance.md) | Pre-generation, caching, the flag-gated improvement programme, and measured results |

## How this relates to the rest of the docs

- **Design dossier (this folder)** — the *why*. Stable, explanatory.
- **`README.md` / `CLAUDE.md`** — how to run and operate it (the *how-to*).
- **`docs/walkthrough.md`** — a code-level tour (browser → backend → DB).
- **`docs/reports/`** — dated audits and the quality/performance roadmap (the
  *current state*).

## One-paragraph summary

AdaptIQ estimates each learner's ability **per concept** using a 1-parameter IRT
model (θ vs. item difficulty β on a shared logit scale), and serves the next
question inside their **Zone of Proximal Development** (a 60–75% success band).
Because generating fact-checked, correctly-difficult questions is slow, that work
is done **ahead of time** by a background worker that keeps Redis queues warm, so
the request path stays instant. Engagement is layered on top with points/levels,
ranked Challenge play, and PvP ELO — all kept separate from the ability estimate
so the game never corrupts the measurement.
