"""Ingestión de la base de conocimiento al vector store.

Uso:  python -m scripts.ingest [--reset]
"""

import argparse
import asyncio
import logging
from pathlib import Path

from app.config.settings import get_settings
from app.rag.embeddings import crear_embedder
from app.rag.ingestion import ingestar
from app.rag.store import ChromaVectorStore

log = logging.getLogger("ingesta")


async def main(reset: bool) -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    embedder = crear_embedder(settings)
    store = await ChromaVectorStore.conectar(settings, embedder.nombre)
    if reset:
        await store.vaciar()
    total = await ingestar(Path(settings.knowledge_dir), store, embedder, settings.rag_chunk_max_chars)
    log.info("Ingestión completa: %d fragmentos (embeddings: %s)", total, embedder.nombre)
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Vacía la colección antes de ingerir")
    args = parser.parse_args()
    asyncio.run(main(args.reset))
