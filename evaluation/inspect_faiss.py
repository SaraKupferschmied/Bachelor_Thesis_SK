from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from chatbot.app.config import settings

INDEX_DIR = ROOT / "chatbot" / "vectorstore" / "faiss_studyplans_docling_table_semantic_new"

embeddings = OllamaEmbeddings(
    model=settings.ollama_embedding_model,
    base_url=settings.ollama_host,
)

db = FAISS.load_local(
    str(INDEX_DIR),
    embeddings,
    allow_dangerous_deserialization=True,
)

print(f"Vectors: {len(db.index_to_docstore_id)}")
print()

docs = list(db.docstore._dict.values())

for i, doc in enumerate(docs[:5], start=1):
    print("=" * 80)
    print(f"DOCUMENT {i}")
    print("=" * 80)

    print("\nMETADATA:")
    for k, v in doc.metadata.items():
        print(f"{k}: {v}")

    print("\nTEXT:")
    print(doc.page_content[:1500])

    print("\n")