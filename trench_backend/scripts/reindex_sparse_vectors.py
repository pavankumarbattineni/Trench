"""Re-encodes the sparse (keyword) half of every stored vector with the
current sparse model (Pinecone's hosted pinecone-sparse-english-v0, see
SparseEncodingService). Needed after switching sparse models: old sparse
vectors (pinecone-text BM25, index_version 2) use a different vocabulary
and would never match new query vectors.

Works entirely from Pinecone: each vector's metadata already holds the
document name, heading path and raw chunk text, so the exact text ingestion
encoded is rebuilt from it -- no re-parsing, no re-reading files from
storage, no new dense (Gemini) embeddings. Only `sparse_values` (and
`index_version` metadata) are updated; dense values stay as they are.

Dry run by default (reports counts, changes nothing); pass --apply to
update. Safe to re-run.

Usage (from trench_backend/):
    uv run python scripts/reindex_sparse_vectors.py            # dry run
    uv run python scripts/reindex_sparse_vectors.py --apply
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pinecone import Pinecone  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.service.sparse_encoding_service import SparseEncodingService  # noqa: E402
from app.service.vector_store_service import HYBRID_DENSE_WEIGHT  # noqa: E402

INDEX_VERSION = 3
_FETCH_BATCH = 100


def contextualized_text(metadata: dict) -> str:
    """The same text DocumentIngestionService encodes: document name and
    heading path, a blank line, then the raw chunk text."""
    header = "\n".join(
        line
        for line in (metadata.get("document_name"), metadata.get("heading_path"))
        if line
    )
    return f"{header}\n\n{metadata.get('text', '')}"


async def reindex(apply: bool) -> None:
    config = get_settings().TRENCH_CONFIG.PINECONE
    index = Pinecone(api_key=config.api_key).Index(config.index_name)
    namespaces = list((index.describe_index_stats().namespaces or {}).keys())
    print(f"Index {config.index_name!r}: {len(namespaces)} namespace(s)")

    total = updated = skipped = 0
    for namespace in namespaces:
        # SDK v10 yields ListItem objects (with .id); older SDKs yield str ids.
        ids = [
            getattr(item, "id", item)
            for page in index.list(namespace=namespace)
            for item in page
        ]
        print(f"  {namespace}: {len(ids)} vector(s)")
        for start in range(0, len(ids), _FETCH_BATCH):
            batch_ids = ids[start : start + _FETCH_BATCH]
            fetched = index.fetch(ids=batch_ids, namespace=namespace).vectors
            records = [
                (vid, fetched[vid].metadata or {})
                for vid in batch_ids
                if vid in fetched
            ]
            sparse_vectors = await SparseEncodingService.encode_documents(
                [contextualized_text(metadata) for _vid, metadata in records]
            )
            for (vid, _metadata), sparse in zip(records, sparse_vectors, strict=True):
                total += 1
                if not sparse["indices"]:
                    # No recognizable words: leave this vector dense-only.
                    skipped += 1
                    continue
                if apply:
                    index.update(
                        id=vid,
                        namespace=namespace,
                        # Same 1 - alpha weighting the vector store applies
                        # on upsert (see scale_hybrid).
                        sparse_values={
                            "indices": sparse["indices"],
                            "values": [
                                v * (1 - HYBRID_DENSE_WEIGHT) for v in sparse["values"]
                            ],
                        },
                        set_metadata={"index_version": INDEX_VERSION},
                    )
                updated += 1

    action = "Updated" if apply else "Would update"
    print(f"{action} {updated} of {total} vector(s); {skipped} skipped (empty sparse).")
    if not apply:
        print("Dry run only -- re-run with --apply to write the changes.")


if __name__ == "__main__":
    asyncio.run(reindex(apply="--apply" in sys.argv))
