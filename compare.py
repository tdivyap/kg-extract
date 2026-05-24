"""
compare.py — Head-to-head: vanilla RAG vs GraphRAG on the same questions.

For each question in eval_set.json, this runs both pipelines and writes a
side-by-side report (JSON + markdown). The markdown is the artifact you can
screenshot for the resume / blog.

RUN:
  uv run python compare.py
"""

import json
import time
from pathlib import Path

from dotenv import load_dotenv

import vanilla_rag
import graph_rag
from graph_build import build_graph

load_dotenv()

EVAL_PATH = Path("eval_set.json")
OUT_JSON = Path("comparison.json")
OUT_MD = Path("comparison.md")


def run_comparison():
    if not EVAL_PATH.exists():
        print(f"❌ {EVAL_PATH} not found.")
        return

    questions = json.loads(EVAL_PATH.read_text())["questions"]

    # Pre-load both stores ONCE so timing measures query latency, not setup.
    print("📦 Loading graph...")
    G = build_graph(Path("graph_raw.json"))
    print(f"   {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.")

    print("📦 Loading / building vector store...")
    store = vanilla_rag.build_index()
    print()

    results = []
    for i, q in enumerate(questions, 1):
        print(f"\n{'=' * 70}")
        print(f"Q{i}: {q['question']}")
        print(f"     (tests: {q['tests']})")
        print(f"{'=' * 70}")

        # ── Vanilla RAG ──
        t0 = time.time()
        rag_result = vanilla_rag.answer(q["question"], store=store)
        rag_time = time.time() - t0
        print(f"\n[vanilla RAG]  {rag_time:.1f}s")
        print(rag_result["answer"][:400] + ("..." if len(rag_result["answer"]) > 400 else ""))

        # ── GraphRAG ──
        t0 = time.time()
        graph_result = graph_rag.answer(q["question"], G=G)
        graph_time = time.time() - t0
        print(f"\n[GraphRAG]     {graph_time:.1f}s")
        print(graph_result["answer"][:400] + ("..." if len(graph_result["answer"]) > 400 else ""))

        results.append({
            "id": q["id"],
            "question": q["question"],
            "tests": q["tests"],
            "expects_entities": q.get("expects_entities", []),
            "vanilla_rag": {
                "answer": rag_result["answer"],
                "retrieved": [c["source"] for c in rag_result["retrieved_chunks"]],
                "latency_s": round(rag_time, 2),
            },
            "graph_rag": {
                "answer": graph_result["answer"],
                "resolved_entities": graph_result["subgraph"]["entities"],
                "paths_found": len(graph_result["subgraph"]["paths"]),
                "latency_s": round(graph_time, 2),
            },
        })

    OUT_JSON.write_text(json.dumps(results, indent=2, default=str))
    write_markdown_report(results)
    print(f"\n✅ Wrote {OUT_JSON} and {OUT_MD}.")
    print("\nRead comparison.md for the human-readable side-by-side.")


def write_markdown_report(results: list[dict]):
    """Write a markdown report — the file you screenshot for the README."""
    lines = ["# GraphRAG vs Vanilla RAG — Head-to-Head\n"]
    lines.append(
        "Each question below tests a known cross-section relationship in the "
        "source corpus. The vanilla-RAG column shows what standard chunk-"
        "similarity retrieval answers when given the same markdown corpus. The "
        "GraphRAG column shows what graph traversal over typed entities and "
        "relations produces.\n"
    )

    for r in results:
        lines.append(f"\n## {r['id']}: {r['question']}")
        lines.append(f"\n*Tests:* {r['tests']}")
        if r.get("expects_entities"):
            lines.append(f"  \n*Should reference:* `{', '.join(r['expects_entities'])}`")

        lines.append(f"\n### Vanilla RAG ({r['vanilla_rag']['latency_s']}s)")
        lines.append(f"\n{r['vanilla_rag']['answer']}\n")

        lines.append(f"\n### GraphRAG ({r['graph_rag']['latency_s']}s)")
        lines.append(
            f"\n*Resolved entities:* "
            f"`{', '.join(r['graph_rag']['resolved_entities']) or '(none)'}` · "
            f"*Paths found:* {r['graph_rag']['paths_found']}\n"
        )
        lines.append(f"\n{r['graph_rag']['answer']}\n")
        lines.append("\n---")

    OUT_MD.write_text("\n".join(lines))


if __name__ == "__main__":
    run_comparison()
