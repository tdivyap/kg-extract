"""
chat.py — Experiment 1: Gradio chat UI over the Chroma vector store.

Original interactive surface from the blog's "Experiment 1 — Vanilla RAG"
section. Equivalent to cells 11-15 of the original Jupyter notebook.

The SYSTEM_PROMPT_TEMPLATE below is preserved VERBATIM from the original
notebook. It contains the named components ("Admission control, cluster
manager, das etc as components") that the blog discusses as the leak — the
prompt is telling the model the answer rather than letting retrieval find it.
This is the historical artifact the blog argues from, not a sanitised version.

PREREQS:
  - Run ingest.py first to build the vector store
  - OPENAI_API_KEY in environment

RUN:
  python chat.py
  # → opens Gradio on http://localhost:7860
"""

import os

from dotenv import load_dotenv
import gradio as gr
from langchain_chroma import Chroma
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI

load_dotenv(override=True)


# ─── CONFIGURATION ────────────────────────────────────────────────────────────
MODEL = "gpt-4.1-mini"
DB_NAME = "vector_db"
EMBEDDING_MODEL_NAME = "mixedbread-ai/mxbai-embed-large-v1"
EMBEDDING_DEVICE = "mps"   # change to "cpu" or "cuda" as appropriate


# ─── THE ORIGINAL SYSTEM PROMPT ───────────────────────────────────────────────
# Preserved verbatim from the original notebook. This prompt is the historical
# artifact for the blog's "Experiment 1" narrative — note how it names
# specific components ("Admission control, cluster manager, das"), which is
# exactly the prompt-leak pattern the blog discusses. The retrieval over the
# Chroma store doesn't reliably surface these terms, so the prompt is
# carrying the domain knowledge instead.

SYSTEM_PROMPT_TEMPLATE = """
You are a knowledgeable, friendly architect helping me in system design
You are chatting with me for designing vsphere HA cluster.
If relevant, use the given context to answer any question.
If you don't know the answer, say so. consider Admission control, cluster manager, das etc as components of vsphere HA.
Do to detailed workflows and link components to give  a holistic detail.
Context:
{context}
"""


def load_vectorstore():
    """Open the Chroma store built by ingest.py."""
    if not os.path.exists(DB_NAME):
        raise SystemExit(
            f"❌ {DB_NAME}/ not found. Run `python ingest.py` first."
        )
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_NAME,
        model_kwargs={"device": EMBEDDING_DEVICE},
        encode_kwargs={"normalize_embeddings": True},
    )
    return Chroma(
        persist_directory=DB_NAME,
        embedding_function=embeddings,
    )


# Initialise once at module load — the embedding model is heavy to load.
vectorstore = load_vectorstore()
retriever = vectorstore.as_retriever()
llm = ChatOpenAI(temperature=0, model_name=MODEL)


def answer_question(question: str, history):
    """Vanilla RAG: retrieve top-k chunks, stuff into prompt, ask LLM."""
    docs = retriever.invoke(question)
    context = "\n\n".join(doc.page_content for doc in docs)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(context=context)
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=question),
    ])
    return response.content


if __name__ == "__main__":
    # Launch a chat UI. The history argument is required by Gradio's
    # ChatInterface signature but unused here — vanilla RAG is stateless.
    gr.ChatInterface(answer_question).launch()
