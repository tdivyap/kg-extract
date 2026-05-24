"""
ingest.py — Experiment 1: build the Chroma vector store.

Original pipeline from the blog's "Experiment 1 — Vanilla RAG" section.
Equivalent to cells 0-7 of the original Jupyter notebook, refactored into a
single executable script.

WHAT THIS DOES:
  1. Loads markdown (and optionally PDF) files from ../../knowledge_base/
  2. Chunks them with RecursiveCharacterTextSplitter (1200/150)
  3. Embeds with mxbai-embed-large-v1 (1024-d) on Mac MPS
  4. Persists a Chroma vector store on disk

DEPENDENCIES (separate venv recommended):
  pip install -r requirements.txt

RUN:
  python ingest.py

NEXT:
  python chat.py    # launches the Gradio chat UI against this vector store
"""

import glob
import os

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_community.document_loaders import (
    DirectoryLoader,
    TextLoader,
    UnstructuredPDFLoader,
)
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv(override=True)


# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DB_NAME = "vector_db"
KNOWLEDGE_BASE_GLOB = "../../knowledge_base/*"

# Chunking: slightly larger than LangChain's default, to keep technical
# sections coherent.
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150

# Embedding model: Mixedbread's 1024-dimensional model. Runs locally on Mac
# MPS (Metal Performance Shaders); change device to 'cpu' on other machines.
# The blog discusses why 1024-d was the sweet spot for this corpus:
#   - 384-d (all-MiniLM-L6-v2) was too thin for technical text
#   - 3072-d (text-embedding-3-large) was slower without quality gains
EMBEDDING_MODEL_NAME = "mixedbread-ai/mxbai-embed-large-v1"
EMBEDDING_DEVICE = "mps"   # change to "cpu" or "cuda" as appropriate


def load_documents():
    """Walk KNOWLEDGE_BASE_GLOB and load markdown + PDF content.

    Each folder under knowledge_base/ becomes a doc_type tag (the original
    notebook used this for color-coding the t-SNE plot). For this project
    we have one folder, so doc_type is uniform.
    """
    folders = glob.glob(KNOWLEDGE_BASE_GLOB)
    all_documents = []

    print(f"Scanning folders in {KNOWLEDGE_BASE_GLOB} ...")

    for folder in folders:
        if not os.path.isdir(folder):
            continue

        doc_type = os.path.basename(folder)
        print(f"  Processing category: {doc_type}")

        # Markdown files
        md_loader = DirectoryLoader(
            folder,
            glob="**/*.md",
            loader_cls=TextLoader,
            use_multithreading=True,
            loader_kwargs={"encoding": "utf-8"},
        )

        # PDF files (using "elements" mode for finer-grained extraction)
        pdf_loader = DirectoryLoader(
            folder,
            glob="**/*.pdf",
            loader_cls=UnstructuredPDFLoader,
            loader_kwargs={"mode": "elements", "strategy": "fast"},
        )

        for loader in (md_loader, pdf_loader):
            raw_docs = loader.load()
            for doc in raw_docs:
                doc.metadata["doc_type"] = doc_type
                # Filter out noise (page numbers, tiny headers)
                if len(doc.page_content) > 30:
                    all_documents.append(doc)

    return all_documents


def build_vectorstore(documents):
    """Chunk, embed, persist."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        add_start_index=True,
    )
    chunks = splitter.split_documents(documents)
    print(f"Loaded {len(documents)} documents → split into {len(chunks)} chunks.")

    if chunks:
        print("─" * 30)
        print(f"Sample chunk metadata: {chunks[0].metadata}")
        print(f"Sample content snippet: {chunks[0].page_content[:200]}...")
        print("─" * 30)

    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_NAME,
        model_kwargs={"device": EMBEDDING_DEVICE},
        encode_kwargs={"normalize_embeddings": True},
    )

    # Wipe any existing collection so re-runs are deterministic.
    if os.path.exists(DB_NAME):
        Chroma(
            persist_directory=DB_NAME,
            embedding_function=embeddings,
        ).delete_collection()

    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=DB_NAME,
    )

    count = vectorstore._collection.count()
    sample_emb = vectorstore._collection.get(
        limit=1, include=["embeddings"],
    )["embeddings"][0]
    dimensions = len(sample_emb)
    print(f"✅ Vector store: {count:,} vectors, {dimensions:,} dimensions → {DB_NAME}/")


if __name__ == "__main__":
    documents = load_documents()
    if not documents:
        print(f"❌ No documents found under {KNOWLEDGE_BASE_GLOB}")
        print(f"   Run the project's ingest.py (Marker) first to populate knowledge_base/")
        raise SystemExit(1)
    build_vectorstore(documents)
