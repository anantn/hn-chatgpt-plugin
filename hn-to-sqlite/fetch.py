import asyncio
import aiohttp
import aiosqlite
from tqdm import tqdm

ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
START_ID = 35650333
END_ID = 1
BATCH_SIZE = 1000
PARALLEL = 100


async def fetch_item(session, id):
    async with session.get(ITEM_URL.format(id=id)) as response:
        if response.status == 200:
            return await response.json()
        return None


async def insert_item(db, item):
    async with db.execute("""
        INSERT OR IGNORE INTO items (id, deleted, type, by, time, text, dead, parent, poll, url, score, title, parts, descendants)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (item['id'], item.get('deleted', None), item['type'], item.get('by', None), item['time'], item.get('text', None),
          item.get('dead', None), item.get('parent', None), item.get(
              'poll', None), item.get('url', None),
          item.get('score', None), item.get('title', None), ','.join(
              map(str, item.get('parts', []))) if item.get('parts') else None,
          item.get('descendants', None))):
        await db.commit()

    if 'kids' in item:
        for order, kid_id in enumerate(item['kids']):
            async with db.execute("""
                INSERT INTO kids (item, kid, display_order)
                VALUES (?, ?, ?)
            """, (item['id'], kid_id, order)):
                await db.commit()


async def main():
    async with aiohttp.ClientSession() as session:
        async with aiosqlite.connect("hn_data.db") as db:
            sem = asyncio.Semaphore(PARALLEL)
            progress_bar = tqdm(total=(START_ID - END_ID),
                                desc="Fetching items", unit="item")

            # Some optimizations for fast inserts at cost of resilience
            await db.execute("PRAGMA synchronous = OFF;")
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA cache_size = 1000000;")
            await db.execute("PRAGMA temp_store = MEMORY;")
            await db.execute("PRAGMA locking_mode = EXCLUSIVE;")

            async def process_item(item_id):
                async with sem:
                    item = await fetch_item(session, item_id)
                    if item:
                        await insert_item(db, item)
                        progress_bar.update(1)

            for i in range(START_ID, END_ID - BATCH_SIZE, -BATCH_SIZE):
                tasks = [process_item(item_id) for item_id in range(
                    i, max(i - BATCH_SIZE, END_ID - 1), -1)]
                await asyncio.gather(*tasks)
            progress_bar.close()

if __name__ == "__main__":
    asyncio.run(main())
