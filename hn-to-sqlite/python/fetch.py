import asyncio
import aiohttp
import aiosqlite
from tqdm import tqdm

ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
START_ID = 35662053
END_ID = 1
BATCH_SIZE = 2048
NUM_WORKERS = 256


async def fetch_item(session, id):
    async with session.get(ITEM_URL.format(id=id)) as response:
        if response.status == 200:
            return await response.json()
        return None


async def insert_items_batch(db, items_batch):
    items_data = []
    kids_data = []

    for item in items_batch:
        deleted = item.get("deleted", None)
        if deleted:
            deleted = True
        items_data.append(
            (
                item["id"],
                deleted,
                item["type"],
                item.get("by", None),
                item["time"],
                item.get("text", None),
                item.get("dead", None),
                item.get("parent", None),
                item.get("poll", None),
                item.get("url", None),
                item.get("score", None),
                item.get("title", None),
                (
                    ",".join(map(str, item.get("parts", [])))
                    if item.get("parts")
                    else None
                ),
                item.get("descendants", None),
            )
        )

        if "kids" in item:
            for order, kid_id in enumerate(item["kids"]):
                kids_data.append((item["id"], kid_id, order))

    async with db.executemany(
        """
        INSERT OR IGNORE INTO items (id, deleted, type, by, time, text, dead, parent, poll, url, score, title, parts, descendants)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        items_data,
    ):
        await db.commit()

    async with db.executemany(
        """
        INSERT INTO kids (item, kid, display_order)
        VALUES (?, ?, ?)
    """,
        kids_data,
    ):
        await db.commit()


async def main():
    connector = aiohttp.TCPConnector(limit=NUM_WORKERS)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with aiosqlite.connect("hn_data.db") as db:
            sem = asyncio.Semaphore(NUM_WORKERS)
            progress_bar = tqdm(total=(START_ID - END_ID))
            # Some optimizations for fast inserts at cost of resilience
            await db.execute("PRAGMA synchronous = OFF;")
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA cache_size = 1000000;")
            await db.execute("PRAGMA temp_store = MEMORY;")
            await db.execute("PRAGMA locking_mode = EXCLUSIVE;")
            await db.execute("PRAGMA mmap_size = 30000000000;")

            async def process_item(item_id):
                async with sem:
                    item = await fetch_item(session, item_id)
                    if item:
                        return item
                    return None

            items_batch = []
            for i in range(START_ID, END_ID - BATCH_SIZE, -BATCH_SIZE):
                tasks = [
                    process_item(item_id)
                    for item_id in range(i, max(i - BATCH_SIZE, END_ID - 1), -1)
                ]
                items = await asyncio.gather(*tasks)
                items_batch.extend([item for item in items if item is not None])
                await insert_items_batch(db, items)
                progress_bar.update(len(items))

            progress_bar.close()


if __name__ == "__main__":
    asyncio.run(main())
