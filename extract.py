"""
extract.py — Stage 1 of the pipeline: structured extraction.

WHAT THIS DOES:
  Reads markdown files in knowledge_base/ (output from a structure-preserving
  parser like Marker)
    → splits each file into sections by heading
    → for each section, calls gpt-4.1-mini with the Pydantic schema enforced
    → writes entities/relations/constraints/parameters to graph_raw.json

PREREQS:
  - .env with OPENAI_API_KEY set
  - knowledge_base/ populated with markdown (run Marker on your PDF first)

RUN:
  uv run python extract.py

EXPECTED COST:  ~$2-5 in gpt-4.1-mini for a 300-page technical book
EXPECTED TIME:  5-15 minutes depending on book size and rate limits
"""

import json
import re
import sys
from pathlib import Path

import instructor
from openai import OpenAI
from dotenv import load_dotenv

from schemas import ExtractionPayload, SCHEMA_VERSION

load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# CLIENT SETUP
# ──────────────────────────────────────────────────────────────────────────────
# instructor.from_openai() wraps the standard OpenAI client and adds the
# `response_model=` parameter. Under the hood it:
#   1. Converts your Pydantic schema to a JSON schema
#   2. Sends it as a "tool" (function calling) to OpenAI
#   3. Parses the model's response back into a Pydantic instance
#   4. If parsing fails (invalid predicate, missing field, etc.) — re-prompts
#      the model with the validation error as feedback. Up to max_retries times.
#
# This retry-with-feedback loop is what makes structured extraction reliable.

client = instructor.from_openai(OpenAI())


# ──────────────────────────────────────────────────────────────────────────────
# THE EXTRACTION PROMPT
# ──────────────────────────────────────────────────────────────────────────────
# Three design decisions worth knowing:
#
# 1. "Do not summarize." We're NOT asking for a description of the section.
#    We're asking for atomic facts. This is the prompt-level enforcement of the
#    "compression mistaken for design" failure mode discussed in the blog.
#
# 2. "If a relation is implied but not stated, still emit it and lower its
#    confidence." Most inter-module dependencies are implied by prose, not
#    stated as "X depends on Y". If you tell the model to only extract explicit
#    relations, you lose the graph structure. Better to extract aggressively
#    with lower confidence and filter later.
#
# 3. The ID convention is in the prompt so entity IDs stay consistent across
#    sections. Without it, the model invents 'master_module_1234' in one
#    section and 'master_mod' in another — defeating deduplication.

EXTRACTION_SYSTEM = """You are a knowledge-extraction engine. Read one section
of a technical document and emit ONLY data matching the provided schema.
Do not summarize.

Extract:
- Entities (Components, Mechanisms, Parameters, FailureModes, Concepts, Actors)
- Relations using ONLY the allowed predicates
- Constraints (invariants stated or strongly implied)
- Parameters (with defaults, ranges, and what they configure)

For every item include provenance (the given source_id, section, version) and
a confidence score in [0,1]. If a relation is implied but not explicitly
stated, emit it and lower its confidence. Do not invent facts not grounded
in the text.

Stable ID convention (always use these):
- Components:   comp.<snake_case>
- Mechanisms:   mech.<snake_case>
- Parameters:   param.<exact_name>
- FailureModes: fm.<snake_case>
- Concepts:     concept.<snake_case>
- Actors:       actor.<snake_case>
"""


# ──────────────────────────────────────────────────────────────────────────────
# STRUCTURAL CHUNKING (split by markdown heading, not by character count)
# ──────────────────────────────────────────────────────────────────────────────
# A naive RAG pipeline using RecursiveCharacterTextSplitter splits on
# character boundaries. A section about Component X might split mid-paragraph.
#
# Here we split on actual MARKDOWN HEADINGS (H1/H2/H3). Each chunk is one
# coherent topic. This works because Marker preserves document structure
# as markdown headings.

def split_by_heading(markdown: str) -> list[tuple[str, str]]:
    """Split markdown into (section_title, section_text) tuples at H1/H2/H3."""
    sections = []
    current_title = "preamble"  # text before the first heading
    current_lines: list[str] = []

    for line in markdown.split("\n"):
        # Match lines like "## Section title" — 1 to 3 # marks then text.
        m = re.match(r"^(#{1,3})\s+(.+)$", line)
        if m:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = m.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Last section (no trailing heading triggers the flush).
    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))

    return sections


# ──────────────────────────────────────────────────────────────────────────────
# THE LLM CALL
# ──────────────────────────────────────────────────────────────────────────────
# Notes:
#
# - temperature=0.1 — extraction, not creativity. Low-but-not-zero gives
#   slightly more robust output than 0.0 (which can get stuck in degenerate
#   loops on edge cases).
#
# - response_model=ExtractionPayload — Instructor uses this to (a) build the
#   JSON schema, (b) parse the response, (c) validate, (d) retry on failure.
#
# - max_retries=2 — Instructor re-prompts with validation errors twice.
#   Each retry is another API call but usually fixes the issue.
#
# - Model: gpt-4.1-mini is the sweet spot for high-volume extraction.
#   Escalate to gpt-4.1 / gpt-4o for sections that consistently fail validation.

def extract_section(
    text: str, source_id: str, section: str, version: str
) -> ExtractionPayload:
    """Run one section through the extractor. Returns a validated payload."""
    user_msg = f"""source_id: {source_id}
section: {section}
product_version: {version}
schema_version: {SCHEMA_VERSION}

SECTION TEXT:
{text}

Extract entities, relations, constraints, and parameters now."""

    return client.chat.completions.create(
        model="gpt-4.1-mini",
        response_model=ExtractionPayload,
        temperature=0.1,
        max_retries=2,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )


# ──────────────────────────────────────────────────────────────────────────────
# THE TOP-LEVEL DRIVER
# ──────────────────────────────────────────────────────────────────────────────
# Walks every markdown file, splits into sections, runs extraction,
# accumulates everything into one JSON blob.
#
# DESIGN NOTES:
#
# - len(body) < 200: skips tiny sections (mostly headings). They produce noisy
#   extractions because the model invents stuff from too little context.
#
# - try/except around extract_section: rate-limit errors, validation failures
#   after retries, or transient API errors fail one section but don't kill
#   the whole run.
#
# - We aggregate in memory and write once at the end. Fine for a single book.
#   For multi-book corpora you'd want streaming writes.

def stamp_schema_version(item: dict) -> dict:
    """Force every Provenance to carry the current SCHEMA_VERSION.

    The LLM may or may not emit the field; we never trust it from the LLM
    side anyway. The pipeline stamps it authoritatively.
    """
    for prov in item.get("provenance", []) or []:
        if isinstance(prov, dict):
            prov["schema_version"] = SCHEMA_VERSION
    return item


def run(
    markdown_dir: Path,
    source_id: str,
    version: str,
    out: Path,
):
    """Extract from every markdown file under markdown_dir into one JSON output."""
    if not markdown_dir.exists() or not any(markdown_dir.rglob("*.md")):
        print(f"❌ No markdown files found under {markdown_dir}/")
        print("   Run a structure-preserving parser (Marker) on your PDF first.")
        sys.exit(1)

    agg = {
        "schema_version": SCHEMA_VERSION,
        "entities": [],
        "relations": [],
        "constraints": [],
        "parameters": [],
    }
    section_count = 0
    fail_count = 0

    # rglob finds *.md recursively. sorted() makes runs deterministic.
    for md in sorted(markdown_dir.rglob("*.md")):
        print(f"\n📄 {md.name}")
        for title, body in split_by_heading(md.read_text()):
            # Skip tiny sections — not enough context to extract meaningfully.
            if len(body) < 200:
                continue

            section_count += 1
            print(f"  → {title[:70]}")

            try:
                payload = extract_section(
                    text=body,
                    source_id=source_id,
                    # Embed filename + heading so provenance is specific:
                    # e.g. "ch3#Master Election"
                    section=f"{md.stem}#{title}",
                    version=version,
                )
                for k in ("entities", "relations", "constraints", "parameters"):
                    for item in getattr(payload, k):
                        d = item.model_dump()
                        stamp_schema_version(d)
                        agg[k].append(d)
            except Exception as e:
                fail_count += 1
                print(f"    ⚠ failed: {e}")

    out.write_text(json.dumps(agg, indent=2, default=str))
    print(
        f"\n✅ Done. Processed {section_count} sections ({fail_count} failed).\n"
        f"   Wrote {len(agg['entities'])} entities, "
        f"{len(agg['relations'])} relations, "
        f"{len(agg['constraints'])} constraints, "
        f"{len(agg['parameters'])} parameters → {out}\n"
        f"   Schema version: {SCHEMA_VERSION}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────
# When you extract multiple sources later, each gets its own run() call with
# a different source_id and version. The source_id links a node back to your
# corpus register.

if __name__ == "__main__":
    run(
        markdown_dir=Path("knowledge_base"),
        # CHANGE THESE TO MATCH YOUR CORPUS:
        source_id="reference_doc",
        version="1.x",
        out=Path("graph_raw.json"),
    )
