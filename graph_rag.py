"""
graph_rag.py — Query the graph, then synthesize an answer.

This is the CHALLENGER. It uses graph_build.py's primitives to find the
relevant subgraph for a question, then asks an LLM to write a natural-language
answer grounded in that subgraph.

The contrast with vanilla_rag.py is the whole point of the project:
  vanilla_rag.py → retrieves CHUNKS by vector similarity → LLM summarizes them
  graph_rag.py   → retrieves SUBGRAPH by entity traversal → LLM explains it

RUN:
  uv run python graph_rag.py "How does component A relate to component B?"
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from graph_build import build_graph, neighbors_with_edges, find_path

load_dotenv()
client = OpenAI()

LLM_MODEL = "gpt-4o"  # synthesis only — same model as vanilla baseline for fair comparison


# ──────────────────────────────────────────────────────────────────────────────
# ENTITY RESOLUTION FROM A QUESTION
# ──────────────────────────────────────────────────────────────────────────────
# Given a natural-language question, find which graph nodes it mentions.
#
# MVP approach: ask the LLM to pick from the list of known entity IDs. Cheap
# and surprisingly reliable when entity IDs use a consistent convention
# (comp.x, mech.y, etc.).
#
# Production upgrade: use embedding similarity over entity names and aliases
# to find candidates, then optionally LLM-disambiguate. The pure-LLM approach
# here breaks down once the entity list exceeds ~200 items.

ROUTER_SYSTEM = """You are an entity router. Given a user question and a list
of known entity IDs from a knowledge graph, return ONLY the IDs the question
is asking about. Output JSON: {"entities": ["comp.x", ...]}.

If the question asks about a relationship between two things, include both.
If you're unsure, include candidates — being inclusive is better than missing
the entity entirely. Never invent IDs that aren't in the provided list."""


def resolve_entities(question: str, G) -> list[str]:
    """Ask the LLM which known entity IDs the question refers to."""
    entity_list = "\n".join(
        f"  {nid} ({data.get('type')}): {data.get('name')}"
        for nid, data in G.nodes(data=True)
    )

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM},
            {
                "role": "user",
                "content": f"Known entities:\n{entity_list}\n\nQuestion: {question}",
            },
        ],
    )
    payload = json.loads(response.choices[0].message.content)
    return [e for e in payload.get("entities", []) if e in G]


# ──────────────────────────────────────────────────────────────────────────────
# SUBGRAPH RETRIEVAL
# ──────────────────────────────────────────────────────────────────────────────
# For each resolved entity, pull its 1-hop neighborhood. If the question
# mentioned 2+ entities, also try to find the shortest path between them —
# this is the magic for "how does X relate to Y" questions.

def get_subgraph_context(question: str, G) -> dict:
    """Return graph information relevant to the question. Bounded size."""
    entities = resolve_entities(question, G)[:4]   # cap at 4 to bound context
    if not entities:
        return {"entities": [], "neighborhoods": [], "paths": []}

    # Slim down each neighborhood — keep only what synthesis needs.
    neighborhoods = []
    for e in entities:
        nbr = neighbors_with_edges(G, e)
        slim = {
            "id": nbr["id"],
            "name": nbr["name"],
            "type": nbr["type"],
            "outgoing": {},
            "incoming": {},
        }
        # Keep top 2 edges per predicate, drop verbose evidence
        for pred, edges in (nbr.get("outgoing") or {}).items():
            slim["outgoing"][pred] = [
                {"to": ed["to_name"], "description": ed["description"][:160]}
                for ed in edges[:2]
            ]
        for pred, edges in (nbr.get("incoming") or {}).items():
            slim["incoming"][pred] = [
                {"from": ed["from_name"], "description": ed["description"][:160]}
                for ed in edges[:2]
            ]
        neighborhoods.append(slim)

    # Limit paths to first 3 entity pairs only
    paths = []
    pair_count = 0
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            if pair_count >= 3:
                break
            path = find_path(G, entities[i], entities[j])
            if path:
                slim_path = [
                    {"from": h["from"], "to": h["to"], "predicate": h["predicate"]}
                    for h in path
                ]
                paths.append({"from": entities[i], "to": entities[j], "hops": slim_path})
            pair_count += 1
        if pair_count >= 3:
            break

    return {"entities": entities, "neighborhoods": neighborhoods, "paths": paths}


# ──────────────────────────────────────────────────────────────────────────────
# SYNTHESIS
# ──────────────────────────────────────────────────────────────────────────────
# The synthesis LLM gets the subgraph as structured JSON, NOT prose chunks.
# The prompt makes the model explain the relationships explicitly — and cite
# the provenance from the edges, not hallucinated sections.

SYNTHESIS_SYSTEM = """You are a technical writer answering questions about a
technical system, grounded in a knowledge graph extracted from its
documentation.

You will be given a JSON subgraph containing entities, their incoming and
outgoing relationships, and (if relevant) the shortest paths between entities
the user asked about. Each edge has a predicate (depends-on, triggers,
performs, etc.) and provenance (which section of the source document supports
it).

Write a clear, grounded answer that:
1. Names the specific entities and relationships from the subgraph.
2. Walks the path / edges when the question is about a relationship.
3. Cites the source sections from the `evidence` fields.
4. Says explicitly when the subgraph doesn't contain enough to answer.

Do NOT invent components or relationships not present in the subgraph."""


def answer(question: str, G=None) -> dict:
    """Answer a question via graph traversal + LLM synthesis."""
    if G is None:
        G = build_graph(Path("graph_raw.json"))

    subgraph = get_subgraph_context(question, G)

    if not subgraph["entities"]:
        return {
            "answer": (
                "The graph doesn't seem to contain entities matching your "
                "question. Try naming a specific component, mechanism, or "
                "parameter from the source."
            ),
            "subgraph": subgraph,
        }

    response = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYNTHESIS_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Subgraph:\n{json.dumps(subgraph, indent=2, default=str)}"
                    f"\n\nQuestion: {question}"
                ),
            },
        ],
    )

    return {
        "answer": response.choices[0].message.content,
        "subgraph": subgraph,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: uv run python graph_rag.py "your question here"')
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    result = answer(question)
    print("\n" + "=" * 70)
    print("ANSWER")
    print("=" * 70)
    print(result["answer"])
    print("\n" + "=" * 70)
    print("RESOLVED ENTITIES")
    print("=" * 70)
    print(", ".join(result["subgraph"]["entities"]) or "(none)")
    if result["subgraph"]["paths"]:
        print("\n" + "=" * 70)
        print("PATHS FOUND")
        print("=" * 70)
        for p in result["subgraph"]["paths"]:
            chain = " → ".join([p["from"]] + [hop["to"] for hop in p["hops"]])
            print(f"  {chain}")
            for hop in p["hops"]:
                print(f"    --[{hop['predicate']}]--> {hop['to']}")
