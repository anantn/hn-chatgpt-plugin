import sys
import asyncio
import aiohttp
import aiosqlite
from tqdm import tqdm
from fetch import insert_items_batch

ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
BATCH_SIZE = 2048
NUM_WORKERS = 256
MISSING_IDS_FILE = "missing_ids.txt"


def load_missing_ids():
    try:
        with open(MISSING_IDS_FILE, "r") as f:
            return {int(line.strip()) for line in f if line.strip()}
    except FileNotFoundError:
        return set()


async def fetch_item(session, id):
    async with session.get(ITEM_URL.format(id=id)) as response:
        if response.status == 200:
            return await response.json()
        elif response.status == 404:
            return "MISSING"
        return None


async def main():
    known_missing_ids = load_missing_ids()
    connector = aiohttp.TCPConnector(limit=NUM_WORKERS)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with aiosqlite.connect("hn_data.db") as db:
            sem = asyncio.Semaphore(NUM_WORKERS)

            async def process_item(item_id):
                if item_id in known_missing_ids:
                    return None
                async with sem:
                    item = await fetch_item(session, item_id)
                    if item == "MISSING":
                        known_missing_ids.add(item_id)
                        return None
                    return item

            async with aiosqlite.connect(
                f"file:{sys.argv[1]}?mode=ro", uri=True
            ) as db_read:
                cursor = await db_read.execute("SELECT id FROM items")
                rows = await cursor.fetchall()
                existing_ids = {row[0] for row in rows}
            max_id = max(existing_ids) if existing_ids else 0
            missing_ids = sorted(set(range(1, max_id + 1)) - existing_ids, reverse=True)

            progress_bar = tqdm(total=len(missing_ids), desc="Filling missing IDs")
            for i in range(0, len(missing_ids), BATCH_SIZE):
                batch_ids = missing_ids[i : i + BATCH_SIZE]
                tasks = [process_item(item_id) for item_id in batch_ids]
                items = await asyncio.gather(*tasks)
                items = [item for item in items if item is not None]
                if items:
                    await insert_items_batch(db, items)
                progress_bar.update(BATCH_SIZE)
            progress_bar.close()

        with open(MISSING_IDS_FILE, "w") as f:
            for mid in sorted(known_missing_ids):
                f.write(f"{mid}\n")


if __name__ == "__main__":
    asyncio.run(main())
