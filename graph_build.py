"""
graph_build.py — Load graph_raw.json into a NetworkX DiGraph and provide queries.

WHAT THIS DOES:
  - Loads graph_raw.json (output of extract.py)
  - Builds an in-memory directed graph using NetworkX
  - Provides query functions: dependencies, paths, predicate lookups, statistics
  - Implements the simplest entity-resolution: merge entities whose normalized
    IDs match (covers the obvious cases like 'comp.x' appearing in 10 sections)

WHEN TO UPGRADE TO NEO4J:
  - The graph exceeds ~50k edges (query latency starts to bite)
  - You want Cypher's pattern-matching for complex graph queries
  - You want graph visualization in Neo4j Bloom
  Until then, NetworkX is faster to iterate on.

RUN:
  uv run python graph_build.py                           # stats
  uv run python graph_build.py --query comp.x            # one node
  uv run python graph_build.py --path comp.a comp.b      # path between two
  uv run python graph_build.py --predicate depends-on    # all edges by type
"""

import argparse
import json
from collections import defaultdict, Counter
from pathlib import Path

import networkx as nx


# ──────────────────────────────────────────────────────────────────────────────
# LOAD & BUILD
# ──────────────────────────────────────────────────────────────────────────────
# NetworkX's MultiDiGraph allows multiple edges between the same two nodes
# (e.g. two sources both say A -depends-on-> B). We keep all of them so
# provenance evidence accumulates rather than overwriting.

def build_graph(raw_path: Path) -> nx.MultiDiGraph:
    """Load extracted JSON and build the graph."""
    if not raw_path.exists():
        raise FileNotFoundError(
            f"{raw_path} not found. Run `uv run python extract.py` first."
        )

    raw = json.loads(raw_path.read_text())
    G = nx.MultiDiGraph()
    G.graph["schema_version"] = raw.get("schema_version", "unknown")

    # ── ENTITY RESOLUTION (simple version) ────────────────────────────────────
    # We group entities by their stable ID. Each occurrence in the corpus is
    # one "vote" — we merge all provenances, take the union of aliases, average
    # the confidence. The ID convention from the extraction prompt is what
    # makes this work: as long as the model emitted "comp.x" consistently,
    # we end up with one node per real entity even if it was mentioned in
    # 30 sections.
    #
    # PRODUCTION upgrade: also use embedding-similarity to merge near-duplicates
    # whose IDs differ. For the MVP the ID-based merge catches most cases.

    by_id: dict[str, dict] = defaultdict(lambda: {
        "type": None, "name": None, "aliases": set(),
        "descriptions": [], "provenance": [], "confidences": [],
    })
    for e in raw["entities"]:
        slot = by_id[e["id"]]
        slot["type"] = e["type"]
        slot["name"] = e["name"]
        slot["aliases"].update(e.get("aliases", []))
        slot["descriptions"].append(e["description"])
        slot["provenance"].extend(e["provenance"])
        slot["confidences"].append(e["confidence"])

    for eid, slot in by_id.items():
        G.add_node(
            eid,
            type=slot["type"],
            name=slot["name"],
            aliases=sorted(slot["aliases"]),
            description=slot["descriptions"][0],
            all_descriptions=slot["descriptions"],
            provenance=slot["provenance"],
            confidence=sum(slot["confidences"]) / len(slot["confidences"]),
            mention_count=len(slot["confidences"]),
        )

    # ── EDGES ─────────────────────────────────────────────────────────────────
    # Edges go in as-is. We don't dedupe edges in the MVP — multiple supporting
    # provenances is a feature, not a bug.

    edge_skipped = 0
    for r in raw["relations"]:
        # Skip edges pointing to nodes we never extracted as entities.
        # The model occasionally references something it didn't define.
        # These "dangling references" are diagnostic data — useful for finding
        # gaps in the corpus.
        if r["subject"] not in G or r["object"] not in G:
            edge_skipped += 1
            continue
        G.add_edge(
            r["subject"], r["object"],
            key=r["id"],
            predicate=r["predicate"],
            modality=r["modality"],
            description=r["description"],
            provenance=r["provenance"],
            confidence=r["confidence"],
        )

    G.graph["edges_skipped_dangling"] = edge_skipped
    G.graph["raw_entity_count"] = len(raw["entities"])
    G.graph["constraint_count"] = len(raw["constraints"])
    G.graph["parameter_count"] = len(raw["parameters"])
    return G


# ──────────────────────────────────────────────────────────────────────────────
# QUERY PRIMITIVES
# ──────────────────────────────────────────────────────────────────────────────
# These are the building blocks compare.py uses to answer "graph-side"
# questions. Each returns plain data structures so the synthesis LLM can
# consume them as text.

def neighbors_with_edges(G: nx.MultiDiGraph, node: str) -> dict:
    """All outgoing and incoming edges for one node, grouped by predicate."""
    if node not in G:
        return {
            "error": f"Node '{node}' not in graph",
            "available": _suggest(G, node),
        }

    out_by_pred = defaultdict(list)
    for _, dst, _, data in G.out_edges(node, keys=True, data=True):
        out_by_pred[data["predicate"]].append({
            "to": dst,
            "to_name": G.nodes[dst].get("name", dst),
            "modality": data["modality"],
            "confidence": data["confidence"],
            "description": data["description"],
            "evidence": data["provenance"][:2],
        })

    in_by_pred = defaultdict(list)
    for src, _, _, data in G.in_edges(node, keys=True, data=True):
        in_by_pred[data["predicate"]].append({
            "from": src,
            "from_name": G.nodes[src].get("name", src),
            "modality": data["modality"],
            "confidence": data["confidence"],
            "description": data["description"],
            "evidence": data["provenance"][:2],
        })

    return {
        "id": node,
        "name": G.nodes[node].get("name", node),
        "type": G.nodes[node].get("type"),
        "description": G.nodes[node].get("description"),
        "outgoing": dict(out_by_pred),
        "incoming": dict(in_by_pred),
    }


def find_path(G: nx.MultiDiGraph, src: str, dst: str, max_len: int = 4) -> list:
    """Find a path between two nodes (undirected projection for explanation)."""
    if src not in G or dst not in G:
        return []
    UG = G.to_undirected(as_view=True)
    try:
        nodes_in_path = nx.shortest_path(UG, src, dst)
    except nx.NetworkXNoPath:
        return []
    if len(nodes_in_path) - 1 > max_len:
        return []

    hops = []
    for a, b in zip(nodes_in_path, nodes_in_path[1:]):
        edges = list(G.get_edge_data(a, b, default={}).values()) + \
                list(G.get_edge_data(b, a, default={}).values())
        edge = max(edges, key=lambda e: e.get("confidence", 0)) if edges else {}
        hops.append({
            "from": a,
            "to": b,
            "predicate": edge.get("predicate", "?"),
            "description": edge.get("description", ""),
            "evidence": edge.get("provenance", [])[:1],
        })
    return hops


def by_predicate(G: nx.MultiDiGraph, predicate: str) -> list:
    """All edges with a given predicate."""
    out = []
    for src, dst, data in G.edges(data=True):
        if data["predicate"] == predicate:
            out.append({
                "from": src,
                "to": dst,
                "modality": data["modality"],
                "confidence": data["confidence"],
                "description": data["description"],
            })
    return out


def stats(G: nx.MultiDiGraph) -> dict:
    """Graph-wide statistics for debugging and for the README."""
    type_counts = Counter(d.get("type") for _, d in G.nodes(data=True))
    pred_counts = Counter(d["predicate"] for _, _, d in G.edges(data=True))
    return {
        "schema_version": G.graph.get("schema_version"),
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "entities_raw": G.graph.get("raw_entity_count"),
        "merged_to": G.number_of_nodes(),
        "edges_skipped_dangling": G.graph.get("edges_skipped_dangling"),
        "constraints": G.graph.get("constraint_count"),
        "parameters": G.graph.get("parameter_count"),
        "by_type": dict(type_counts),
        "by_predicate": dict(pred_counts),
    }


def _suggest(G: nx.MultiDiGraph, missing_id: str, k: int = 5) -> list[str]:
    """Cheap typo-helper: suggest similar IDs when a lookup misses."""
    base = missing_id.split(".")[-1].lower()
    candidates = [
        n for n in G.nodes
        if base in n.lower() or n.lower() in missing_id.lower()
    ]
    return candidates[:k]


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=Path("graph_raw.json"))
    ap.add_argument("--query", help="Node ID to explore, e.g. comp.x")
    ap.add_argument(
        "--path", nargs=2, metavar=("FROM", "TO"),
        help="Find a path between two node IDs"
    )
    ap.add_argument("--predicate", help="List all edges with this predicate")
    args = ap.parse_args()

    G = build_graph(args.raw)

    if args.query:
        print(json.dumps(neighbors_with_edges(G, args.query), indent=2, default=str))
    elif args.path:
        print(json.dumps(find_path(G, args.path[0], args.path[1]), indent=2, default=str))
    elif args.predicate:
        print(json.dumps(by_predicate(G, args.predicate), indent=2, default=str))
    else:
        print(json.dumps(stats(G), indent=2))
