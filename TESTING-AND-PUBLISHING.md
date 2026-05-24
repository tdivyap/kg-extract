# Testing and Publishing Runbook

The exact, ordered steps from a fresh download to a public GitHub repo.

This is the operational sequence — every command, every checkpoint, every
thing that can go wrong. Total expected time: 1.5–2 hours of focused work,
plus parser runtime.

---

## Phase 0 — Prerequisites (10 minutes)

Verify on your machine:

```bash
python3 --version             # need 3.11 or higher
uv --version                  # if missing: curl -LsSf https://astral.sh/uv/install.sh | sh
git --version
gh --version                  # optional but recommended (GitHub CLI)
```

If `gh` is missing, install it: <https://cli.github.com/> — it makes the
push step a one-liner.

You will also need:
- An OpenAI API key with at least $10 of credit
- A source PDF you have the right to use
- ~1 GB of disk space

---

## Phase 1 — Local setup (5 minutes)

```bash
# Unzip the project somewhere you'll keep it
unzip kg-extract.zip
cd kg-extract

# Install dependencies
uv sync

# Configure your API key
cp .env.example .env
# Edit .env, paste your OPENAI_API_KEY

# Verify imports
uv run python -c "from schemas import ExtractionPayload, SCHEMA_VERSION; print('ok', SCHEMA_VERSION)"
```

**Checkpoint:** the last command prints `ok 1.0.0`.

If you get an `ImportError`, run `uv sync` again. If you get `OPENAI_API_KEY
not set`, your `.env` file isn't being read — check it's at the repo root and
contains a non-empty key.

---

## Phase 2 — PDF to Markdown (10–30 minutes, machine-dependent)

This project doesn't bundle a PDF parser — it has heavy ML dependencies. Run
Marker (recommended) in a separate environment:

```bash
# In a SEPARATE directory, not kg-extract/
mkdir -p ~/marker-env && cd ~/marker-env
uv venv && source .venv/bin/activate
uv pip install marker-pdf

# Run Marker on your PDF
marker_single /path/to/your-source.pdf
# Marker writes output to a folder named after the PDF
```

Copy Marker's output into the project's `knowledge_base/`:

```bash
cp -r /path/to/marker/output/* /path/to/kg-extract/knowledge_base/
```

**Checkpoint:**

```bash
cd /path/to/kg-extract
find knowledge_base -name "*.md" | wc -l
# should print a non-zero number
head -50 knowledge_base/*.md | head -30
# you should see real prose with H1/H2/H3 headings
```

If you see gibberish, Marker didn't parse the PDF cleanly — try different
Marker options before continuing.

---

## Phase 3 — Customise the source_id (1 minute)

Open `extract.py`. At the bottom, change `source_id` and `version` to match
your corpus:

```python
if __name__ == "__main__":
    run(
        markdown_dir=Path("knowledge_base"),
        source_id="my_reference_doc",   # ← name your corpus
        version="1.x",                    # ← what version of the source
        out=Path("graph_raw.json"),
    )
```

The `source_id` is what links every extracted node back to your corpus
register. Use a stable identifier; never change it after extraction starts.

---

## Phase 4 — Smoke test (3 minutes, ~$0.50)

**Do this before paying for the full extraction.** It catches schema/prompt
issues cheaply.

```bash
# Move all but one markdown file out of the way temporarily
mkdir _stash
mv knowledge_base/*.md _stash/ 2>/dev/null
# Pick one meaty file to test with — the second or third chapter is usually best
mv _stash/<your-chapter-3-file>.md knowledge_base/

# Extract just this one file
uv run python extract.py
```

**Checkpoint:**

```bash
ls -lh graph_raw.json

uv run python -c "
import json
g = json.load(open('graph_raw.json'))
print(f\"Schema version: {g.get('schema_version')}\")
for k in ('entities', 'relations', 'constraints', 'parameters'):
    print(f'  {k}: {len(g[k])}')"
```

You want to see:
```
Schema version: 1.0.0
  entities: 25-50
  relations: 30-80
  constraints: 2-8
  parameters: 0-15
```

If you see entities but zero relations, the extraction prompt isn't pulling
out relationships. Re-read the EXTRACTION_SYSTEM prompt in `extract.py` and
check the predicate enum in `schemas.py`.

If everything looks good, restore the rest of the corpus:

```bash
rm graph_raw.json
mv _stash/*.md knowledge_base/
rmdir _stash
```

---

## Phase 5 — Full extraction (~10 minutes, $2–5)

```bash
uv run python extract.py
```

Watch for `⚠ failed:` lines. A handful is fine (the model occasionally tries
an invalid predicate). If >10% of sections fail, the prompt or schema needs
adjustment.

**Checkpoint:**
- Failure rate under 10%
- At least 100 entities and 150 relations
- `graph_raw.json` between 200 KB and 2 MB

---

## Phase 6 — Inspect the graph (5 minutes)

```bash
# Top-level stats
uv run python graph_build.py
```

Look for: a reasonable mix of entity types, a healthy spread of predicates
(not 95% one predicate), `edges_skipped_dangling` under ~20% of total.

```bash
# Inspect a specific node — pick one you know matters in your corpus
uv run python graph_build.py --query comp.your_node_id

# Try a path between two important nodes — this is the make-or-break test
uv run python graph_build.py --path comp.a comp.b

# List all edges of one predicate
uv run python graph_build.py --predicate depends-on
```

If a path exists where you expected one, the methodology works on your corpus.
**Take a screenshot — this is your demo evidence.**

If a critical relationship is missing, the extraction missed it. Two fixes:
re-extract with stricter prompting (edit `EXTRACTION_SYSTEM` in
`extract.py`), or hand-author the missing edges into `graph_raw.json` and
document this as a known limitation.

---

## Phase 7 — Customise the eval set (5 minutes)

Open `eval_set.json`. The default questions are generic placeholders. Replace
with **5 cross-section questions specific to your corpus** — questions whose
answers require integrating facts from different chapters.

Examples of good questions (substitute your domain):
- "How does X relate to Y during failover?"
- "What parameters affect the timing of Z?"
- "What happens if A loses connection to B?"

Bad questions (vanilla RAG handles these too):
- "What is X?" — answerable from one section
- "Define Y" — definitional questions don't test cross-section reasoning

---

## Phase 8 — Run the head-to-head (~5 minutes, $0.50)

```bash
uv run python compare.py
```

This runs both pipelines on each question and writes:
- `comparison.json` — machine-readable
- `comparison.md` — **your demo artifact**

Read `comparison.md`. For at least 3 of 5 questions, GraphRAG should:
1. Name specific entity IDs
2. State the relationship using a schema predicate
3. Cite specific sections from provenance
4. Explain the *connection*, not just describe each side

For the same questions, vanilla RAG should plausibly miss the connection or
mention it weakly without grounding.

**This file is what goes on your resume and blog as the headline artifact.**

---

## Phase 9 — Pre-publish hygiene (10 minutes)

Before pushing to GitHub, verify the repo is publishable:

```bash
# Confirm no secrets, no derived artifacts will get committed
git init 2>/dev/null
git status
```

The output should show:
- `.env.example` ✓ (template, fine)
- `.gitignore` ✓
- `*.py`, `*.md`, `pyproject.toml` ✓ (source code, fine)

The output should **NOT** show:
- `.env` (your secret key)
- `graph_raw.json` (derived from copyrighted source)
- `chroma_db/` (derived index)
- `knowledge_base/` contents (copyrighted source material)
- `pdfs/` contents

If anything in the second group appears, the `.gitignore` isn't catching it.
Verify:

```bash
cat .gitignore
# Should include: .env, graph_raw.json, comparison.json, comparison.md, chroma_db/, knowledge_base/, pdfs/
```

---

## Phase 10 — Push to GitHub (5 minutes)

If you have the GitHub CLI installed:

```bash
git add .
git status                              # FINAL check — confirm what's being committed
git commit -m "Initial public release of kg-extract"

gh repo create kg-extract \
  --public \
  --description "Typed knowledge-graph extraction over technical documentation. GraphRAG that recovers cross-section dependencies vanilla RAG cannot reconstruct." \
  --source=. \
  --remote=origin \
  --push
```

The repo is now at `https://github.com/<your-username>/kg-extract`.

### Without the GitHub CLI

```bash
# Create the repo manually on github.com first (don't initialize with README or .gitignore — we have our own)

git add .
git status
git commit -m "Initial public release of kg-extract"
git branch -M main
git remote add origin https://github.com/<your-username>/kg-extract.git
git push -u origin main
```

---

## Phase 11 — Update the blog and resume (5 minutes)

The blog and resume both reference `github.com/divyatp/kg-extract`. Verify
those links now resolve:

```bash
curl -I https://github.com/<your-username>/kg-extract
# Should return HTTP/2 200
```

If the URL is different from what's in the blog/resume:

- **Hashnode blog:** Dashboard → Edit post → search for the URL → update → Update post
- **Resume:** Re-run the build script with the new URL, or manually edit the docx

---

## Phase 12 — Add the first concrete result (10 minutes)

Open the blog. In the Experiment 3 callout, replace generic phrasing with a
real number from your `comparison.md`:

> *Result · Recovered the cross-section relationships the first two experiments lost. On 4 of 5 evaluation questions, GraphRAG correctly named both endpoint entities and the connecting predicate; vanilla RAG named both endpoints in 1 of 5.*

Do the same in the resume's Selected Work section. Replace the bracketed
`[X / Y]` and `[W / Y]` placeholders with your actual numbers.

This single edit converts the blog from "thoughtful narrative" to "thoughtful
narrative with evidence." Highest-leverage change you can make post-publish.

---

## Phase 13 — Share (when ready)

Only after Phases 10–12 are complete:

1. LinkedIn post pointing at the Hashnode URL — see the publishing guide in
   the blog package for the suggested framing
2. Pin the post to your LinkedIn profile
3. Update your resume's project line to reference both the blog and the
   GitHub repo

---

## When to call for help

Three points where you might want a second pair of eyes:

**After Phase 5** — share the output of `uv run python graph_build.py` (the
stats). If something looks off about the distribution of types or predicates,
diagnose before running the comparison.

**After Phase 6** — share the output of the `--path` query you most care
about. If no path exists, figure out why before going further.

**After Phase 8** — share `comparison.md`. If the contrast between vanilla
RAG and GraphRAG is weak, iterate on the eval questions or the graph
retrieval before publishing.

---

## Maintenance after first release

Three rules:

1. **Never push `graph_raw.json` to the public repo** even if it would be
   convenient. The `.gitignore` enforces this; check that it's working after
   each new commit.

2. **Schema changes go through `SCHEMA.md` first.** Read it, understand the
   change-type categories, bump `SCHEMA_VERSION` appropriately. See the
   Schema evolution section in SCHEMA.md.

3. **Iterate the eval set.** As you learn what your corpus is good at, add
   questions that highlight the strengths and expose remaining limitations.
   `comparison.md` is more credible with 10 strong questions than 5 weak
   ones.
