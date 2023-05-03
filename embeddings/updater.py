import json
import copy
import asyncio
import aiohttp
import requests

from tqdm import tqdm
from aiohttp_sse_client.client import EventSource

from utils import log


class SyncService:
    RETRY = 5  # seconds
    BATCH_SIZE = 64
    HN_URL = "https://hacker-news.firebaseio.com/v0"
    EMBED_REALTIME_FREQ = 900  # seconds

    def __init__(self, db_conn, embed_conn, telemetry, offset, doc_encoder, catchup=True, embed_realtime=True):
        self.db_conn = db_conn
        self.embed_conn = embed_conn
        self.offset = offset
        self.doc_encoder = doc_encoder
        self.catchup = catchup

        self.buffer = []
        self.disconnect = False
        self.initial_fetch_completed = False
        self.affected_stories = set()
        self.telemetry = telemetry

        self.search_index = None
        self.embedding_task = None
        self.embed_realtime = embed_realtime

    async def run(self):
        updates = None
        if self.catchup:
            updates = asyncio.create_task(self.watch_updates())

        async with aiohttp.ClientSession(read_timeout=5, conn_timeout=5) as session:
            if self.catchup:
                log("Fetching max item ID for catching up...")
                max_item_id = self.get_max_item_id()
                max_item_id_from_db = self.get_max_item_id_from_db()
                start_id = max(max_item_id_from_db - self.offset, 1)

                log(f"Fetching items from ID {start_id} to {max_item_id}")
                await self.fetch_and_insert_items(session, start_id, max_item_id)
                log(
                    f"Finished initial fetch, now inserting updates (buffered {len(self.buffer)})")
            self.initial_fetch_completed = True

        if self.embed_realtime:
            self.embedding_task = asyncio.create_task(
                self.process_affected_stories())
        return updates

    async def shutdown(self):
        log("Shutting down SSE channel...")
        self.disconnect = True
        if self.embedding_task:
            log("Cancelling embedding updater...")
            try:
                self.embedding_task.cancel()
                await self.embedding_task
            except asyncio.CancelledError:
                pass

    def get_max_item_id_from_db(self):
        cursor = self.db_conn.cursor()
        cursor.execute("SELECT MAX(id) as maxId FROM items")
        row = cursor.fetchone()
        cursor.close()
        return row[0] or 0

    def get_max_item_id(self):
        response = requests.get(f'{self.HN_URL}/maxitem.json')
        return response.json()

    async def fetch_item(self, session, id):
        async with session.get(f'{self.HN_URL}/item/{id}.json') as response:
            return await response.json()

    async def fetch_user(self, session, id):
        async with session.get(f'{self.HN_URL}/user/{id}.json') as response:
            return await response.json()

    def insert_items(self, items):
        cursor = self.db_conn.cursor()
        for item in items:
            if not item:
                continue
            parts = None
            if item.get("parts"):
                if isinstance(item["parts"], list):
                    parts = ",".join(str(i) for i in item["parts"])
                else:
                    parts = str(item["parts"])

            cursor.execute("""
            INSERT OR REPLACE INTO items
                (id, deleted, type, by, time, text, dead, parent,
                 poll, url, score, title, parts, descendants)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (item["id"], item.get("deleted"), item["type"], item.get("by"), item["time"], item.get("text"),
                            item.get("dead"), item.get("parent"), item.get(
                                "poll"), item.get("url"), item.get("score"),
                            item.get("title"), parts, item.get("descendants")))

            if item.get("kids"):
                for order, kid_id in enumerate(item["kids"]):
                    cursor.execute("""
                    INSERT OR REPLACE INTO kids
                        (item, kid, display_order)
                    VALUES (?, ?, ?)
                    """, (item["id"], kid_id, order))

            self.db_conn.commit()
        cursor.close()

    def insert_users(self, users):
        cursor = self.db_conn.cursor()
        for user in users:
            if not user:
                continue

            submitted = None
            if user.get("submitted"):
                if isinstance(user["submitted"], list):
                    submitted = ",".join(str(i) for i in user["submitted"])
                elif isinstance(user["submitted"], int):
                    submitted = str(user["submitted"])

            cursor.execute("""
            INSERT OR REPLACE INTO users
                (id, created, karma, about, submitted)
            VALUES (?, ?, ?, ?, ?)""",
                           (user["id"], user["created"], user["karma"], user.get("about"), submitted))

        self.db_conn.commit()
        cursor.close()

    async def fetch_and_insert_items(self, session, start_id, end_id):
        progress_bar = tqdm(total=(end_id - start_id + 1))
        for i in range(start_id, end_id + 1, self.BATCH_SIZE):
            retry_count = 0
            max_retries = 5
            retry_delay = 5
            while retry_count < max_retries:
                try:
                    fetched_items = await asyncio.gather(*[self.fetch_item(session, i + j) for j in range(self.BATCH_SIZE)])
                    self.insert_items(fetched_items)
                    progress_bar.update(self.BATCH_SIZE)
                    break
                except (aiohttp.client_exceptions.ClientConnectorError,
                        aiohttp.client_exceptions.ServerTimeoutError) as e:
                    await asyncio.sleep(retry_delay)
                    retry_count += 1
        progress_bar.close()

    def find_story_id_for_item(self, item_id):
        cursor = self.db_conn.cursor()
        cursor.execute(
            """
            WITH RECURSIVE item_hierarchy(id, parent) AS (
                SELECT i.id, i.parent
                FROM items i
                WHERE i.id = ?
                UNION ALL
                SELECT i.id, i.parent
                FROM items i
                JOIN item_hierarchy ih ON i.id = ih.parent
                WHERE i.type IN ('comment', 'story')
            )
            SELECT id
            FROM item_hierarchy
            WHERE parent IS NULL
            """,
            (item_id,)
        )
        story = cursor.fetchone()
        cursor.close()
        return story["id"] if story else None

    def extract_affected_stories(self, item_ids):
        affected = set()
        for item_id in item_ids:
            story_id = self.find_story_id_for_item(item_id)
            if story_id:
                affected.add(story_id)
        return affected

    async def process_affected_stories(self):
        while not self.disconnect:
            if len(self.affected_stories) > 0 and self.embed_realtime:
                to_process = copy.copy(self.affected_stories)
                self.telemetry.inc("total_affected_stories", len(to_process))
                self.affected_stories.clear()
                # log(
                #    f"Processing affected stories for realtime embed: {len(to_process)}")
                processed = await self.doc_encoder.process_stories(to_process)
                self.telemetry.inc("total_embedded_stories", len(processed))
                # log(
                #    f"{len(processed)} affected stories were interesting, embeddings created")
                if self.search_index:
                    self.search_index.update_embeddings(processed)
                else:
                    log(
                        f"WARNING: could not update FAISS index!")
            await asyncio.sleep(self.EMBED_REALTIME_FREQ)

    async def process_updates(self):
        items = []
        profiles = []

        for updates in self.buffer:
            if "items" in updates["data"]:
                items.extend(updates["data"]["items"])
            if "profiles" in updates["data"]:
                profiles.extend(updates["data"]["profiles"])

        # Fetch and insert all items as a batch
        self.telemetry.inc("items_updated", len(items))
        async with aiohttp.ClientSession() as session:
            items_chunks = [items[i:i + self.BATCH_SIZE]
                            for i in range(0, len(items), self.BATCH_SIZE)]
            for chunk in items_chunks:
                fetched_items = await asyncio.gather(*[self.fetch_item(session, item_id) for item_id in chunk])
                self.insert_items(fetched_items)

        # Fetch and insert all user profiles as a batch
        self.telemetry.inc("users_updated", len(profiles))
        async with aiohttp.ClientSession() as session:
            profiles_chunks = [profiles[i:i + self.BATCH_SIZE]
                               for i in range(0, len(profiles), self.BATCH_SIZE)]
            for chunk in profiles_chunks:
                fetched_profiles = await asyncio.gather(*[self.fetch_user(session, profile_id) for profile_id in chunk])
                self.insert_users(fetched_profiles)

        self.affected_stories.update(self.extract_affected_stories(items))

    async def watch_updates(self):
        while not self.disconnect:
            try:
                async with EventSource(f"{self.HN_URL}/updates.json", timeout=-1) as client:
                    async for event in client:
                        if self.disconnect:
                            break
                        updates = json.loads(event.data)
                        if updates:
                            self.telemetry.inc("updates")
                            self.buffer.append(updates)
                            if self.initial_fetch_completed:
                                await self.process_updates()
                                self.buffer.clear()
                            else:
                                log(
                                    f"Buffer now at {len(self.buffer)}.")
            except (aiohttp.client_exceptions.ClientConnectorError,
                    aiohttp.client_exceptions.ClientOSError,
                    TimeoutError) as e:
                await asyncio.sleep(self.RETRY)
