# Multidomain Screening & Classification Prototype

**LLM-Assisted Multidomain Screening and Classification System for ADHD and
Cognitive Impairment — research prototype.**

> ⚠️ **This is a research screening prototype, not a diagnostic system.**
> It never claims to clinically diagnose ADHD, MCI, dementia, or any other
> disorder. Every output is labeled a *screening classification*, not a
> diagnosis, and every result page states this explicitly.

---

## What this is

A 30-item questionnaire covering attention, hyperactivity/impulsivity,
developmental history, memory, executive function, language, orientation,
cognitive decline, and functional independence. Responses are collapsed into
ten **domain scores**, which a transparent, rule-based classifier maps to one
of five research categories:

1. Probable ADHD
2. Probable MCI
3. Probable Dementia
4. Likely Healthy
5. Other / Indeterminate

```
Questionnaire
      │
Structured feature extraction (deterministic, always available)
      │
LLM-assisted interpretation (optional — same schema, used only if
requested and configured; falls back to deterministic on any failure)
      │
Domain-level feature vector (10 features, 0–4 each)
      │
Deterministic rule-based classifier
      │
Five-category screening result + confidence + rationale + disclaimer
```

The LLM, when enabled, is only ever asked to convert answers into the same
structured feature schema the deterministic scorer produces — it is never
asked to diagnose.

## Project layout

```
api/
  index.py            The ENTIRE backend: questionnaire data, domain
                       scoring, the rule-based classifier, optional LLM
                       extraction, and the two API routes -- all in one
                       file, plus the routes that serve public/ as the
                       frontend.
public/
  index.html           One-question-at-a-time questionnaire + results UI
  style.css            Design system (IBM Plex type trio, instrument panel)
  app.js               Client logic: fetch questions, drive flow, submit,
                        render the results "signal panel"
vercel.json             Intentionally empty ({}) -- zero config
requirements.txt         Flask only
```

### Why one file?

Vercel's current Python runtime expects **exactly one entrypoint** — a file
named `app.py`, `index.py`, `server.py`, `main.py`, `wsgi.py`, or `asgi.py`,
at the project root or inside `src/`, `app/`, or `api/`, exporting a
top-level `app` object (Flask/FastAPI/WSGI/ASGI). It deploys that as a
**single** Vercel Function and routes every request to it — it does not
create one function per file the way older Python-on-Vercel setups did.

Putting the whole backend in `api/index.py` sidesteps every cross-folder
import question entirely (no `lib/` vs `_lib/`, no root-directory
mismatches) — there is nothing to misplace. `api/index.py` also serves
`public/` directly via Flask routes (`GET /` and a catch-all static route),
so there's no separate reliance on Vercel's static-file detection either.

## Running locally

```bash
pip install -r requirements.txt
python3 -c "from api.index import app; app.run(debug=True, port=3000)"
```

Or with the Vercel CLI, which runs it exactly as Vercel will in production:

```bash
npm install -g vercel
vercel dev
```

Then open `http://localhost:3000`.

## Deploying to Vercel

**Repo structure matters.** `api/`, `public/`, `vercel.json`, and
`requirements.txt` must sit at the root of whatever Vercel treats as the
project (its **Root Directory** setting). If your GitHub repo has these
files nested inside a subfolder, either:

- move the contents of that subfolder up to the repo root, **or**
- set **Project Settings → General → Root Directory** in Vercel to the path
  of that subfolder.

Then:

```bash
git add .
git commit -m "Consolidate backend into single Flask entrypoint"
git push
```

In the Vercel dashboard: **Deployments → Redeploy** (uncheck "Use existing
Build Cache" the first time, so it picks up the new `requirements.txt` and
folder structure). Vercel auto-detects the Flask app in `api/index.py` and
deploys it as a single Python function; no build command is required.

## Environment variables (optional)

The system works fully **without** any environment variables — it just uses
deterministic questionnaire scoring.

| Variable          | Required | Purpose                                                   |
|-------------------|----------|------------------------------------------------------------|
| `OPENAI_API_KEY`  | No       | Enables the optional LLM feature-extraction path           |
| `OPENAI_MODEL`    | No       | Overrides the default model (`gpt-4o-mini`)                 |

Set these in the Vercel dashboard under **Project → Settings →
Environment Variables**. Never commit a `.env` file — `.gitignore` already
excludes it, and the key is only ever read server-side (`os.environ`); it is
never sent to or embedded in frontend JavaScript.

If `use_llm: true` is sent to `/api/screen` but no key is configured, or the
LLM call fails or returns invalid JSON for any reason, the system silently
and automatically falls back to deterministic scoring — the participant
always gets a result.

The frontend now exposes this as a checkbox on the intro screen ("Use
LLM-assisted feature extraction"). Checking it sends `use_llm: true`;
leaving it unchecked (the default) sends `use_llm: false`. Either way, the
results screen states which extraction method actually ran for that
submission — including telling the participant explicitly if LLM extraction
was requested but fell back to deterministic scoring — so nothing about
which path ran is hidden. **Set `OPENAI_API_KEY` in Vercel before checking
this box in production, or every submission will fall back regardless of
the toggle.**

## API reference

### `GET /api/questions`

Returns the questionnaire, section metadata, and scale label sets.

### `POST /api/screen`

Request:

```json
{
  "answers": { "1": 3, "2": 2, "...": "...", "30": 0 },
  "use_llm": false
}
```

`answers` must include all 30 question ids (string or numeric keys) with
integer values 0–4. `use_llm` is optional, defaults to `false`.

Response:

```json
{
  "label": "Probable ADHD",
  "confidence": 0.78,
  "scores": { "Probable ADHD": 3.1, "Probable MCI": 1.4, "Probable Dementia": 0.9, "Likely Healthy": 1.8 },
  "domains": { "attention": 3.4, "...": "..." },
  "contributing_domains": ["attention", "childhood_history"],
  "rationale": "The response pattern shows elevated attention-related difficulties...",
  "disclaimer": "This is a research screening result, not a clinical diagnosis. ...",
  "meta": { "extraction_method": "deterministic" }
}
```

`confidence` is a **prototype/model confidence**, not a calibrated clinical
probability. Error responses are always `{"error": "<participant-safe
message>"}` with an appropriate status code; internal exceptions, stack
traces, and API keys are never included in a response.

## Classifier design

See the top of `api/index.py` for the full, commented implementation. In
brief: ten domain scores (means of their mapped questionnaire items, never a
flat sum of all 30), four weighted evidence scores, then explicit guardrails
(minimum evidence threshold, ambiguity margin, an ADHD developmental-history
requirement, and functional impairment as the MCI/Dementia differentiator)
decide the final label. Nothing is a black box — the full evidence breakdown
is always returned. Weights and thresholds are prototype defaults, not
fitted to data, and are centralized as named constants for easy revision.

## Privacy & security

- No name, phone number, address, government ID, or other identifying
  information is ever requested by the questionnaire.
- Participant responses are **not stored** — `/api/screen` is stateless.
- `OPENAI_API_KEY` is read only from server-side environment variables and
  never appears in any frontend code or client response.
- `.env` files are excluded via `.gitignore` — never commit one.

## Research roadmap (not yet implemented)

This prototype ships the questionnaire, the rule-based classifier, and the
API/UI. Explicitly out of scope for this codebase today:

- A labeled dataset with independently-established reference labels (never
  the LLM's own output used as ground truth, to avoid circular evaluation).
- Training/evaluating ML classifiers (logistic regression, SVM, random
  forest, XGBoost) on a held-out test set with accuracy, precision, recall,
  macro-F1, confusion matrices, calibration, and error analysis.
- A hybrid LLM + rule/ML comparison across the four-way evaluation plan.

No claim of DSM-5 validation, clinical validation, or diagnostic accuracy
should be added anywhere in this project unless backed by that evidence.
