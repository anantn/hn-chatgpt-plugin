import os
import re
import uvicorn
import asyncio
import sqlite3

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from typing import Optional

import search
import updater
import embedder
from utils import log, print_db_stats, LogPhase, Telemetry

OPTS = os.getenv("OPTS")
DB_PATH = os.getenv("DB_PATH")
PORT = 8001

app = FastAPI()
telemetry = Telemetry()
encoder, doc_embedder, sync_service, search_index = None, None, None, None


@app.get("/search")
async def search_api(query: str, top_k: Optional[int] = search.Index.TOP_K):
    return await search_index.search(query, top_k=top_k)


@app.get("/health", response_class=HTMLResponse)
async def health_api(update_db: Optional[bool] = False):
    report = telemetry.report(update_db=update_db)
    with open(os.path.join(os.path.dirname(__file__), "health.html"), "r") as file:
        html_template = file.read()

    html_content = html_template.format(
        db_metrics="".join(
            [
                f"<tr><td>{key}</td><td>{value}</td></tr>"
                for key, value in report["db"].items()
            ]
        ),
        counter_metrics="".join(
            [
                f"<tr><td>{key}</td><td>{value}</td></tr>"
                for key, value in report["counters"].items()
            ]
        ),
        time_metrics="".join(
            [
                f"<tr><td>{key}</td><td>{value}</td></tr>"
                for key, value in report["times"].items()
            ]
        ),
        memory_metrics="".join(
            [
                f"<tr><td>{key}</td><td>{value}</td></tr>"
                for key, value in report["memory"].items()
            ]
        ),
        flag_metrics="".join(
            [
                f"<tr><td>{key}</td><td>{value}</td></tr>"
                for key, value in report["flags"].items()
            ]
        ),
    )

    return HTMLResponse(content=html_content, status_code=200)


@app.post("/toggle")
async def toggle_api():
    global sync_service
    sync_service.embed_realtime = not sync_service.embed_realtime
    return {"embed_realtime": sync_service.embed_realtime}


async def main(db_conn, embed_conn):
    global encoder, doc_embedder, sync_service, search_index

    # Parse options if available
    dosync = False if OPTS and "nosync" in OPTS else True
    offset = (
        int(re.search(r"offset=(\d+)", OPTS).group(1))
        if OPTS and "offset=" in OPTS
        else 1000
    )

    # Load embedder
    lp = LogPhase("loaded embedder")
    encoder = embedder.Embedder()
    await encoder.encode(["hello hacker news"])
    doc_embedder = embedder.DocumentEmbedder(db_conn, embed_conn, encoder)
    lp.stop()

    # Start sync service
    lp = LogPhase("loaded syncservice")
    log("catching up on data updates...")
    sync_service = updater.SyncService(
        db_conn,
        telemetry,
        offset,
        doc_embedder,
        catchup=dosync,
        embed_realtime=False,
    )
    updates = await sync_service.run()
    lp.stop()

    # Load vector search
    lp = LogPhase("loaded vector search index")
    log("creating vector index...")
    search_index = search.Index(db_conn, embed_conn, encoder)
    sync_service.search_index = search_index
    lp.stop()

    # Start API server
    telemetry.connect(db_conn, embed_conn, sync_service, encoder)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info", reload=True)
    )
    uvicorn_task = asyncio.create_task(server.serve())

    if dosync:
        _, pending = await asyncio.wait(
            {updates, uvicorn_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    else:
        await uvicorn_task

    print("Exiting...")
    await sync_service.shutdown()
    await encoder.shutdown()
    db_conn.close()
    embed_conn.close()


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

    print_db_stats(db_conn, embed_conn)
    asyncio.run(main(db_conn, embed_conn))
