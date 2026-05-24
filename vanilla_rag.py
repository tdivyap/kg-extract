"""
vanilla_rag.py — The baseline RAG, deliberately un-tuned.

This is the BASELINE the project is going to beat. Same markdown corpus,
similar chunking parameters, Chroma vector store, OpenAI embeddings, plain
LLM synthesis with no architectural prompt.

The point of this file is to be the HONEST baseline. Do NOT:
  - Add reranking
  - Add hybrid search
  - Sneak in a system prompt that names specific components
  - Pre-filter chunks by metadata

The whole project's claim is "GraphRAG beats this configuration on
cross-section dependency queries." That claim is only meaningful if the
baseline is the standard recipe a developer would reach for first.

If you want to demonstrate a TUNED RAG comparison, do that as a separate
experiment with its own file — not by quietly improving this one.

RUN:
  uv run python vanilla_rag.py "How does component A relate to component B?"

NOTE: For the original LangChain + Chroma + mxbai-embed-large + Gradio
implementation that started this project, see experiments/01_vanilla_rag/.
That experiment uses local embeddings (Mac MPS) and a Gradio UI; this file
uses OpenAI embeddings for portability.
"""

import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
DB_PATH = "chroma_db"
KB_PATH = "knowledge_base"
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
EMBEDDING_MODEL = "text-embedding-3-small"   # 1536-dim, ~$0.02 / 1M tokens
LLM_MODEL = "gpt-4o"                          # same model as graph path uses

# Deliberately plain system prompt. It tells the model to use the context
# and admit when it doesn't know. It does NOT name specific components,
# because that would smuggle our own expertise into the baseline and inflate
# its scores. (This is the failure mode of the "Architect Prompt" in
# experiments/02_page_index/.)

BASELINE_SYSTEM = """You are a helpful technical assistant. Answer the user's
question using ONLY the context below. If the context doesn't contain the
answer, say so explicitly. Cite relevant sections by quoting brief snippets.

Context:
{context}
"""


# ──────────────────────────────────────────────────────────────────────────────
# INDEX BUILD (one-time)
# ──────────────────────────────────────────────────────────────────────────────

def build_index(force: bool = False) -> Chroma:
    """Build or load the Chroma vector store."""
    if Path(DB_PATH).exists() and not force:
        print(f"📦 Loading existing index from {DB_PATH}/")
        return Chroma(
            persist_directory=DB_PATH,
            embedding_function=OpenAIEmbeddings(model=EMBEDDING_MODEL),
        )

    if Path(DB_PATH).exists():
        shutil.rmtree(DB_PATH)

    print(f"🔧 Building index from {KB_PATH}/ ...")

    loader = DirectoryLoader(
        KB_PATH,
        glob="**/*.md",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
    )
    documents = loader.load()

    if not documents:
        print(f"❌ No markdown found under {KB_PATH}/. Run Marker first.")
        sys.exit(1)

    print(f"   Loaded {len(documents)} markdown files.")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        add_start_index=True,
    )
    chunks = splitter.split_documents(documents)
    print(f"   Split into {len(chunks)} chunks.")

    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    store = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=DB_PATH,
    )
    print(f"✅ Indexed {store._collection.count()} chunks → {DB_PATH}/")
    return store


# ──────────────────────────────────────────────────────────────────────────────
# QUERY
# ──────────────────────────────────────────────────────────────────────────────

def answer(question: str, k: int = 5, store: Chroma | None = None) -> dict:
    """Answer a question via vanilla RAG. Returns answer text + chunks."""
    if store is None:
        store = build_index()

    retriever = store.as_retriever(search_kwargs={"k": k})
    docs = retriever.invoke(question)

    context_blocks = []
    for i, d in enumerate(docs, 1):
        src = d.metadata.get("source", "unknown").split("/")[-1]
        context_blocks.append(f"[{i}] from {src}:\n{d.page_content}")
    context = "\n\n---\n\n".join(context_blocks)

    llm = ChatOpenAI(model=LLM_MODEL, temperature=0.1)
    response = llm.invoke([
        SystemMessage(content=BASELINE_SYSTEM.format(context=context)),
        HumanMessage(content=question),
    ])

    return {
        "answer": response.content,
        "retrieved_chunks": [
            {
                "source": d.metadata.get("source", "unknown").split("/")[-1],
                "snippet": d.page_content[:200] + "...",
            }
            for d in docs
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: uv run python vanilla_rag.py "your question here"')
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    result = answer(question)
    print("\n" + "=" * 70)
    print("ANSWER")
    print("=" * 70)
    print(result["answer"])
    print("\n" + "=" * 70)
    print("RETRIEVED CHUNKS")
    print("=" * 70)
    for i, c in enumerate(result["retrieved_chunks"], 1):
        print(f"\n[{i}] {c['source']}")
        print(f"    {c['snippet']}")
