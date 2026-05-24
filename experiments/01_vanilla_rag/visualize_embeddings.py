"""
visualize_embeddings.py — Optional: plot the Chroma vectors in 3D.

Equivalent to cells 8-10 of the original Jupyter notebook. The t-SNE plot
was useful during the original experiment to see whether the embeddings
were clustering meaningfully (they were — clean clustering by topic) before
the deeper retrieval failures were diagnosed.

This script is OPTIONAL. The main demonstration runs without it.

DEPENDENCIES:
  pip install scikit-learn plotly numpy

RUN (after ingest.py):
  python visualize_embeddings.py
  # → opens an interactive plot in your browser
"""

import numpy as np
import plotly.graph_objects as go
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from sklearn.manifold import TSNE


# Match ingest.py
DB_NAME = "vector_db"
EMBEDDING_MODEL_NAME = "mixedbread-ai/mxbai-embed-large-v1"
EMBEDDING_DEVICE = "mps"


def main():
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_NAME,
        model_kwargs={"device": EMBEDDING_DEVICE},
        encode_kwargs={"normalize_embeddings": True},
    )
    vectorstore = Chroma(persist_directory=DB_NAME, embedding_function=embeddings)
    collection = vectorstore._collection

    result = collection.get(include=["embeddings", "documents", "metadatas"])
    vectors = np.array(result["embeddings"])
    documents = result["documents"]
    metadatas = result["metadatas"]

    if len(vectors) == 0:
        print("No vectors found. Run ingest.py first.")
        return

    # All docs are one corpus here — tag uniformly. Adapt this if you split
    # the knowledge_base into multiple categorised folders.
    doc_types = [m.get("doc_type", "default") for m in metadatas]
    category_colors = {"default": "steelblue"}
    colors = [category_colors.get(str(t).lower(), "gray") for t in doc_types]

    # t-SNE down to 3D. Perplexity capped to avoid sklearn errors on small sets.
    tsne = TSNE(
        n_components=3,
        random_state=42,
        perplexity=min(30, max(2, len(vectors) - 1)),
    )
    reduced = tsne.fit_transform(vectors)

    fig = go.Figure(data=[go.Scatter3d(
        x=reduced[:, 0], y=reduced[:, 1], z=reduced[:, 2],
        mode="markers",
        marker=dict(
            size=6,
            color=colors,
            opacity=0.8,
            line=dict(width=0.5, color="white"),
        ),
        text=[
            f"<b>Source:</b> {m.get('source', 'unknown')}<br>"
            f"<b>Snippet:</b> {d[:150]}..."
            for d, m in zip(documents, metadatas)
        ],
        hoverinfo="text",
    )])

    fig.update_layout(
        title="Knowledge base: 3D embedding space (t-SNE projection)",
        scene=dict(
            xaxis=dict(showgrid=True, zeroline=False, showticklabels=False, title="Semantic X"),
            yaxis=dict(showgrid=True, zeroline=False, showticklabels=False, title="Semantic Y"),
            zaxis=dict(showgrid=True, zeroline=False, showticklabels=False, title="Semantic Z"),
        ),
        width=1000,
        height=800,
        margin=dict(r=0, b=0, l=0, t=50),
        template="plotly_dark",
    )

    fig.show()


if __name__ == "__main__":
    main()
