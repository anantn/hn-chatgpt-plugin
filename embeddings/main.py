import os
import re
import uvicorn
import asyncio
import sqlite3

from fastapi import FastAPI
from typing import Optional

import search
import updater
import embedder
from utils import log, LogPhase

OPTS = os.getenv("OPTS")
DB_PATH = os.getenv("DB_PATH")
PORT = 8001

app = FastAPI()
encoder, doc_embedder, sync_service, search_index = None, None, None, None


@app.get("/search")
async def search_api(query: str, top_k: Optional[int] = search.Index.TOP_K):
    return await search_index.search(query, top_k=top_k)


async def main(db_conn, embed_conn):
    global encoder, doc_embedder, sync_service, search_index

    # Parse options if available
    dosync = False if OPTS and "nosync" in OPTS else True
    embedrt = False if OPTS and "noembedrt" in OPTS else True
    embedcu = False if OPTS and "noembedcu" in OPTS else True
    offset = int(re.search(r'offset=(\d+)', OPTS).group(1)
                 ) if OPTS and "offset=" in OPTS else 1000

    # Load embedder
    lp = LogPhase("loaded embedder")
    encoder = embedder.Embedder()
    await encoder.encode([["test", "query"]])
    doc_embedder = embedder.DocumentEmbedder(db_conn, embed_conn, encoder)
    lp.stop()

    # Start sync service
    lp = LogPhase("loaded syncservice")
    log("catching up on data updates...")
    sync_service = updater.SyncService(
        db_conn, embed_conn, offset, doc_embedder, catchup=dosync, embed_realtime=embedrt)
    updates = await sync_service.run()
    lp.stop()

    # Catch up on document embeddings
    if embedcu:
        log("catching up on document embeddings...")
        lp = LogPhase("embedding generation catchup")
        await doc_embedder.process_catchup_stories(offset)
        lp.stop()

    # Load vector search
    lp = LogPhase("loaded vector search index")
    log("creating vector index...")
    search_index = search.Index(db_conn, embed_conn, encoder)
    sync_service.search_index = search_index
    lp.stop()

    # Start API server
    server = uvicorn.Server(uvicorn.Config(
        app, port=PORT, log_level="info", reload=True))
    uvicorn_task = asyncio.create_task(server.serve())

    _, pending = await asyncio.wait(
        {updates, uvicorn_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()

    print("Exiting...")
    await sync_service.shutdown()
    await encoder.shutdown()
    db_conn.close()
    embed_conn.close()


def db_state(db_conn, embed_conn):
    cursor = db_conn.cursor()
    cursor.execute("SELECT MAX(id), COUNT(*) FROM items")
    max_id, total = cursor.fetchone()
    cursor.execute("SELECT MAX(id) FROM items WHERE type='story'")
    max_story_id = cursor.fetchone()[0]
    cursor.close()

    cursor = embed_conn.cursor()
    cursor.execute(
        "SELECT MAX(story), COUNT(DISTINCT story), COUNT(*) FROM embeddings")
    max_story, total_doc, total_embed = cursor.fetchone()
    cursor.close()

    print(f"{max_id:8}: Max item in db")
    print(f"{total:8}: Total items in db")
    print(f"{max_story_id:8}: Max story in db")
    print(f"{max_story:8}: Max story embedded")
    print(f"{total_doc:8}: Total docs embedded")
    print(f"{total_embed:8}: Total embeddings\n")


if __name__ == "__main__":
    if not DB_PATH:
        print("Set DB_PATH to path of hn-sqlite.db")
        exit()

    db_path = os.path.expanduser(DB_PATH)
    db_conn = sqlite3.connect(db_path)
    db_conn.row_factory = sqlite3.Row

    prefix = os.path.splitext(db_path)[0]
    embed_conn = sqlite3.connect(f"{prefix}_embeddings.db")
    embed_conn.row_factory = sqlite3.Row

    db_state(db_conn, embed_conn)
    asyncio.run(main(db_conn, embed_conn))
