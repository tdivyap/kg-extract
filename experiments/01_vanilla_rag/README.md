# Experiment 1 — Vanilla RAG

The original RAG pipeline referenced in the [companion blog post](https://systems-ai.hashnode.dev)
as "Experiment 1." Preserved here in its original shape as a historical
artifact for the blog's argument.

## Stack

- LangChain
- Chroma (vector store, persistent)
- `mxbai-embed-large-v1` (Mixedbread, 1024-d, local on Mac MPS)
- GPT-4.1-mini for synthesis
- Gradio for the interactive UI
- t-SNE + Plotly for the optional 3D embedding visualisation

## Files

```
01_vanilla_rag/
├── README.md                  ← this file
├── requirements.txt           ← deps for this experiment (separate from main)
├── ingest.py                  ← load knowledge_base/ → chunk → embed → Chroma
├── chat.py                    ← Gradio UI: retrieve top-k → LLM under preserved prompt
└── visualize_embeddings.py    ← optional 3D t-SNE plot of the embedding space
```

## How to run

This experiment has heavier ML dependencies than the main project (the
embedding model runs locally). Use a separate venv:

```bash
cd experiments/01_vanilla_rag
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Make sure the project-level knowledge_base/ has Marker output already.
# Build the vector store:
python ingest.py

# Optional: visualise the embedding space
python visualize_embeddings.py

# Launch the chat UI
python chat.py
# → http://localhost:7860
```

On non-Apple machines, edit `EMBEDDING_DEVICE` in `ingest.py` and `chat.py`
(change `"mps"` to `"cpu"` or `"cuda"`).

## What this experiment showed

This pipeline worked smoothly on infrastructure. Documents loaded,
embeddings populated, t-SNE clustering looked clean, the Gradio UI was
satisfying.

Single-section questions returned good answers. Questions about
relationships between concepts described in *different sections* returned
plausible-sounding but incomplete responses — the retrieved chunks were
similar to the query in vocabulary but missed the bridging passage with
different vocabulary.

The failure was structural. No amount of better embeddings, hybrid search,
or larger `k` recovered the missing relationships. That diagnosis is what
motivated the GraphRAG approach in the project root.

## A note on the preserved system prompt

The `SYSTEM_PROMPT_TEMPLATE` in `chat.py` is preserved verbatim from the
original notebook, including the explicit naming of components
("Admission control, cluster manager, das"). The blog argues that this
prompt was leaking domain knowledge into the system — telling the model the
answer rather than letting retrieval discover it. The original prompt is
kept as the historical artifact for that argument. Do not sanitise it.

See the blog's "Experiment 1 — Vanilla RAG" section for the full discussion.
