"""
page_index_query.py — Experiment 2 from the companion blog post.

The original pipeline: PyMuPDF-based TOC parsing, GPT-4o-mini for parallel
section summaries, threadpool with semaphore rate-limiting, a router LLM at
query time, then GPT-4o synthesis with the "Architect Prompt."

This script is preserved here as a historical artifact. The blog's "Experiment
2" section discusses what this pipeline does well and what it does NOT do —
namely, the strict prompt smuggles the developer's domain knowledge into the
LLM's instructions, making the output look better than the retrieval actually
warrants. See the blog for the full discussion.

Two functional artifacts here:
  - generate_index_parallel(): builds page_index.json from the PDF's TOC,
    summarising each section with GPT-4o-mini in parallel
  - run_query(user_query): routes the question to relevant sections, pulls
    the full page text for each, and synthesises an answer with GPT-4o under
    the Architect Prompt

The Architect Prompt is reproduced verbatim from the original notebook
because the blog argues that *this specific prompt* (with its named
components and protocols) is the failure-mode worth examining. Sanitising
the prompt would erase the evidence.

PREREQS:
  - A copy of the source PDF locally (path configured below)
  - OPENAI_API_KEY in environment

DEPENDENCIES (separate from main project):
  pip install pymupdf openai python-dotenv

RUN:
  python page_index_query.py
"""

import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore

import fitz  # PyMuPDF
from openai import OpenAI, RateLimitError
from dotenv import load_dotenv

load_dotenv()


# ─── CONFIGURATION ────────────────────────────────────────────────────────────
client = OpenAI()
PDF_FILE_PATH = "../../eb-vmware-vsphere-clustering-deep-dive.pdf"
INDEX_FILE = "page_index.json"

# LIMITS: Tune to your OpenAI tier.
MAX_THREADS = 10
api_semaphore = Semaphore(3)
file_lock = Lock()


# ─── THE "ARCHITECT PROMPT" ───────────────────────────────────────────────────
# Preserved verbatim from the original notebook. This prompt is the artifact
# the blog discusses in "Experiment 2 — Page indexing with a strict prompt":
# the named components and protocols here are what made the LLM output look
# impressive. Stripping the named terminology from this prompt was the
# experiment that revealed the prompt — not the retrieval — was carrying the
# apparent technical depth.
#
# Do NOT improve or generalise this prompt. Its specificity is the point.

ARCHITECT_PROMPT = (
    "You are a VMware Principal Systems Architect. Your task is to write a highly technical Low-Level Design (LLD). "
    "\n\nSTRICT REQUIREMENT: You must establish the 'Technical Handshake' and connective tissue between every component. "
    "Do not provide high-level summaries; focus on the API calls, protocols (SOAP, RPC, UDP), and internal state updates."
    "\n\nREQUIRED TECHNICAL DEPTH:"
    "\n1. FDM (Fault Domain Manager): Detail the Master/Slave election process. Break down HMM (Host Management Module) network heartbeats (UDP 8182) vs. T1/T2 Storage heartbeats. Explain how the VMM (VM Management Module) interacts with the cluster's 'Master List' to track protection states."
    "\n2. vpxd & DAS: Explain the role of the Domain Availability Service (DAS) within vCenter. Map the workflow where DAS coordinates with DRS for placement and Admission Control for slot/percentage validation before issuing a 'FailoverAction'."
    "\n3. hostd (The Executioner): Detail the 'Handshake' between the Resource Manager (checking physical RAM/CPU reservations) and the Config Store (handling .vmx registration and hardware locking)."
    "\n4. vpxa (The Liaison): Explicitly define the VIM API path. vpxa must be shown as the communication pipe that receives vpxd instructions and relays them to the local FDM and hostd agents."
    "\n5. Cluster Manager: Detail how it orchestrates the global inventory state and signals the execution layer on the target hosts."
    "\n\nFORMATTING:"
    "\n- Use '```mermaid' sequenceDiagram for the chronological call flow (Host Failure -> Detection -> Placement -> Execution)."
    "\n- Use '```mermaid' graph TD for a physical sub-module hierarchy."
    "\n- Use bold technical terms for protocols and specific ESXi sub-daemons."
)


# ─── SECTION SUMMARISATION ────────────────────────────────────────────────────
# Each TOC entry gets a one-paragraph summary from GPT-4o-mini. We rate-limit
# with a semaphore so we don't trip OpenAI's per-second cap, and exponential-
# backoff on the rare RateLimitError that slips through.

def get_section_summary_with_backoff(title, sample_text, retries=5):
    """Summarise one section with retry-on-rate-limit."""
    for i in range(retries):
        try:
            with api_semaphore:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{
                        "role": "user",
                        "content": f"Summarize section '{title}': {sample_text}",
                    }],
                    timeout=30,
                )
                return response.choices[0].message.content
        except RateLimitError:
            wait_time = (2 ** i) + random.random()
            time.sleep(wait_time)
        except Exception:
            return None
    return None


def process_single_section(i, entry, next_start_page, total_pages, pdf_path):
    """Open the PDF, pull the first 800 chars of the section, summarise it."""
    _level, title, start_page = entry
    end_page = next_start_page - 1 if next_start_page else total_pages

    try:
        doc = fitz.open(pdf_path)
        page_text = doc.load_page(start_page - 1).get_text()[:800]
        doc.close()
    except Exception:
        return None

    summary = get_section_summary_with_backoff(title, page_text)
    if summary:
        return {
            "id": f"sec_{i}",
            "title": title,
            "pages": [start_page, end_page],
            "summary": summary,
        }
    return None


def generate_index_parallel():
    """Build page_index.json from the PDF's table of contents.

    Idempotent: if INDEX_FILE already exists, only new sections are processed.
    """
    if not os.path.exists(PDF_FILE_PATH):
        print(f"❌ {PDF_FILE_PATH} not found.")
        return

    doc = fitz.open(PDF_FILE_PATH)
    toc = doc.get_toc()
    total_pages = len(doc)
    doc.close()

    existing_data = []
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r") as f:
            existing_data = json.load(f)
    processed_titles = {item["title"] for item in existing_data}

    tasks = []
    for i, entry in enumerate(toc):
        if entry[1] in processed_titles:
            continue
        next_start = toc[i + 1][2] if i + 1 < len(toc) else None
        tasks.append((i, entry, next_start, total_pages, PDF_FILE_PATH))

    if not tasks:
        print("✅ All TOC sections already indexed.")
        return

    print(f"📋 Indexing {len(tasks)} sections in parallel...")
    results = existing_data
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        future_to_title = {
            executor.submit(process_single_section, *task): task[1][1]
            for task in tasks
        }
        for future in as_completed(future_to_title):
            data = future.result()
            if data:
                with file_lock:
                    results.append(data)
                    # Persist incrementally so a crash doesn't lose work.
                    with open(INDEX_FILE, "w") as f:
                        json.dump(results, f, indent=2)
    print(f"✅ Indexed {len(results)} sections → {INDEX_FILE}")


# ─── QUERY ROUTING + SYNTHESIS ────────────────────────────────────────────────
# Step 1: a small router LLM picks which sections are relevant to the question.
# Step 2: we pull 5+ consecutive pages of full text per selected section so the
#         synthesis LLM gets coherent context, not fragments.
# Step 3: GPT-4o synthesises under the Architect Prompt.

def run_query(user_query):
    """Answer one question via page-index routing + full-section retrieval."""
    if not os.path.exists(INDEX_FILE):
        return "Run indexing first."

    with open(INDEX_FILE, "r") as f:
        index_data = json.load(f)

    index_context = "\n".join(
        f"ID: {d['id']} | Title: {d['title']}" for d in index_data
    )

    router_res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "Return a JSON object with a list of IDs: {'ids': ['sec_1']}",
            },
            {
                "role": "user",
                "content": f"Index:\n{index_context}\n\nQuery: {user_query}",
            },
        ],
        response_format={"type": "json_object"},
    )
    target_ids = json.loads(router_res.choices[0].message.content).get("ids", [])

    # CONNECTIVITY LOGIC: pull surrounding pages so the LLM sees flow, not fragments.
    doc = fitz.open(PDF_FILE_PATH)
    lookup = {item["id"]: item for item in index_data}
    context_text = ""

    for tid in target_ids[:6]:
        if tid in lookup:
            start, _end = lookup[tid]["pages"]
            # Pull up to 8 consecutive pages to maintain narrative flow.
            for p_idx in range(start - 1, min(len(doc), start + 7)):
                page_data = doc.load_page(p_idx).get_text()
                context_text += f"\n[SOURCE: Page {p_idx + 1}]\n{page_data}\n"
    doc.close()

    final_res = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": ARCHITECT_PROMPT},
            {
                "role": "user",
                "content": f"Full Technical Context:\n{context_text}\n\nUser Question: {user_query}",
            },
        ],
    )
    return final_res.choices[0].message.content


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Step A: build the page index (idempotent — safe to re-run).
    generate_index_parallel()

    # Step B: ask a deliberately demanding cross-section question.
    test_query = (
        "Provide a Low-Level Design (LLD) focused on the inter-process "
        "communication (IPC) and state machine transitions during a Host "
        "Failure event. "
        "\n\nTECHNICAL REQUIREMENTS:"
        "\n1. Map the 'Handshake' between FDM (Master HMM/VMM) and the "
        "vCenter vpxd Liaison."
        "\n2. Detail how Admission Control 'slots' are validated by the "
        "hostd Resource Manager before the PowerOnVM task is issued."
        "\n3. Explain the role of the Cluster Manager in coordinating "
        "'FailoverAction' and how placement decisions from DRS are pushed "
        "through vpxa to the execution layer."
        "\n4. Explicitly define the protocols used (SOAP, RPC, UDP 8182) "
        "for every component hop."
    )

    print("\n" + "=" * 70)
    print("ANSWER")
    print("=" * 70)
    print(run_query(test_query))
