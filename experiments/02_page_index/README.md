# Experiment 2 — Page indexing with a strict prompt

The page-indexed pipeline referenced in the [companion blog post](https://systems-ai.hashnode.dev)
as "Experiment 2." Preserved here as a historical artifact for the blog's
"Trial two" narrative.

## Stack

- PyMuPDF for TOC parsing
- GPT-4o-mini for parallel section summaries (ThreadPoolExecutor + Semaphore
  rate-limiting + exponential backoff on RateLimitError)
- A router LLM at query time that picks relevant sections from the index
- GPT-4o for final synthesis under the "Architect Prompt"

## Files

```
02_page_index/
├── README.md             ← this file
├── requirements.txt      ← deps for this experiment
└── page_index_query.py   ← build page_index.json, then query under Architect Prompt
```

## How to run

This experiment reads the source PDF *directly* via PyMuPDF (not via
Marker), so it expects the PDF file at the path configured in
`page_index_query.py` (default: `../../eb-vmware-vsphere-clustering-deep-dive.pdf`).

```bash
cd experiments/02_page_index
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Build the index AND run a test query in one shot:
python page_index_query.py
# (The script's __main__ does both.)
```

The index is persistent (`page_index.json`) — subsequent runs skip already-
indexed sections, so re-running is cheap.

## What this experiment showed

The output looked dramatically better than vanilla RAG. Real Mermaid
sequence diagrams. Specific function names. Confident technical depth. For
about a day I was pleased.

Then I tested what was actually driving the quality. I stripped the
specific terminology from the "Architect Prompt" — kept the structural
demands ("produce sequence diagrams") but removed the named components and
protocols. The output collapsed into generic mush.

The "depth" had not come from the retrieved passages. It had come from me,
encoded in the prompt. The pipeline was getting credit for understanding
things I had typed into its instructions.

That was the uncomfortable realisation that motivated the reframe to
typed graph extraction. See the blog's "Experiment 2" and "The diagnosis"
sections.

## A note on the preserved Architect Prompt

The `ARCHITECT_PROMPT` in `page_index_query.py` is preserved verbatim from
the original notebook, including the named protocols and sub-modules
(FDM, vpxd, DAS, hostd, vpxa, etc.). The blog argues that this specific
prompt — its specificity, its named components — is the failure-mode
worth examining. Sanitising it would erase the evidence.

The protocol names and sub-daemon names mentioned here are all publicly
documented in VMware's reference materials. This is the kind of public-doc
terminology that ex-VMware engineers blog about routinely. Nothing
proprietary appears in the prompt.
