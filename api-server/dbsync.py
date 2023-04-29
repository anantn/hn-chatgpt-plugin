import time
import json
import copy
import asyncio
import aiohttp
import sqlite3
import requests
from tqdm import tqdm
from aiohttp_sse_client.client import EventSource

BATCH_SIZE = 64
OFFSET = 10000
HN_URL = "https://hacker-news.firebaseio.com/v0"
conn = None

# Create an asyncio event to signal when the initial fetch is complete
initial_fetch_complete_event = asyncio.Event()


def log_with_timestamp(*args):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[{timestamp}]", *args)


def get_max_item_id_from_db():
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(id) as maxId FROM items")
    row = cursor.fetchone()
    cursor.close()
    return row[0] or 0


def get_max_item_id():
    response = requests.get(f'{HN_URL}/maxitem.json')
    return response.json()


async def fetch_item(session, id):
    async with session.get(f'{HN_URL}/item/{id}.json') as response:
        return await response.json()


async def fetch_user(session, id):
    async with session.get(f'{HN_URL}/user/{id}.json') as response:
        return await response.json()


def insert_items(items):
    cursor = conn.cursor()
    for item in items:
        if not item:
            continue
        parts = ",".join(item["parts"]) if item.get("parts") else None

        cursor.execute("""
        INSERT OR REPLACE INTO items (id, deleted, type, by, time, text, dead, parent, poll, url, score, title, parts, descendants)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (item["id"], item.get("deleted"), item["type"], item.get("by"), item["time"], item.get("text"),
                        item.get("dead"), item.get("parent"), item.get(
                            "poll"), item.get("url"), item.get("score"),
                        item.get("title"), parts, item.get("descendants")))

        if item.get("kids"):
            for order, kid_id in enumerate(item["kids"]):
                cursor.execute("""
                INSERT OR REPLACE INTO kids (item, kid, display_order)
                VALUES (?, ?, ?)
                """, (item["id"], kid_id, order))

        conn.commit()
    cursor.close()


def insert_users(users):
    cursor = conn.cursor()
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
        INSERT OR REPLACE INTO users (id, created, karma, about, submitted)
        VALUES (?, ?, ?, ?, ?)""",
                       (user["id"], user["created"], user["karma"], user.get("about"), submitted))

        conn.commit()
    cursor.close()


async def fetch_and_insert_items(session, start_id, end_id):
    progress_bar = tqdm(total=(end_id - start_id + 1))
    for i in range(start_id, end_id + 1, BATCH_SIZE):
        retry_count = 0
        max_retries = 5
        retry_delay = 5
        while retry_count < max_retries:
            try:
                fetched_items = await asyncio.gather(*[fetch_item(session, i + j) for j in range(BATCH_SIZE)])
                insert_items(fetched_items)
                progress_bar.update(BATCH_SIZE)
                break
            except aiohttp.client_exceptions.ClientConnectorError as e:
                print(
                    f"Connection error: {e}, retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_count += 1
            except Exception as e:
                print(f"Unexpected error: {e}")
                raise e
    progress_bar.close()


buffer = []
disconnect = False
initial_fetch_completed = False
affected_stories = set()


def find_story_id_for_item(item_id):
    cursor = conn.cursor()
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


def extract_affected_stories(item_ids):
    affected = set()
    for item_id in item_ids:
        story_id = find_story_id_for_item(item_id)
        if story_id:
            affected.add(story_id)
    return affected


async def process_affected_stories(encoder):
    global affected_stories
    while not disconnect:
        if len(affected_stories) > 0:
            to_process = copy.copy(affected_stories)
            affected_stories.clear()
            log_with_timestamp(
                f"Processing affected stories: {len(to_process)}")
            await encoder.process_stories(to_process)
            log_with_timestamp("Affected stories queue cleared.")
        await asyncio.sleep(600)


async def process_updates(updates_array, encoder):
    items = []
    profiles = []

    for updates in updates_array:
        if "items" in updates["data"]:
            items.extend(updates["data"]["items"])
        if "profiles" in updates["data"]:
            profiles.extend(updates["data"]["profiles"])

    # Fetch and insert all items as a batch
    async with aiohttp.ClientSession() as session:
        items_chunks = [items[i:i + BATCH_SIZE]
                        for i in range(0, len(items), BATCH_SIZE)]
        for chunk in items_chunks:
            fetched_items = await asyncio.gather(*[fetch_item(session, item_id) for item_id in chunk])
            insert_items(fetched_items)

    # Fetch and insert all user profiles as a batch
    async with aiohttp.ClientSession() as session:
        profiles_chunks = [profiles[i:i + BATCH_SIZE]
                           for i in range(0, len(profiles), BATCH_SIZE)]
        for chunk in profiles_chunks:
            fetched_profiles = await asyncio.gather(*[fetch_user(session, profile_id) for profile_id in chunk])
            insert_users(fetched_profiles)

    log_with_timestamp(
        f"Updated {len(items)} items and {len(profiles)} profiles.")

    # global affected_stories
    # affected = extract_affected_stories(items)
    # log_with_timestamp(
    #     f"Updates impacted {len(affected)} stories, adding to set.")
    # affected_stories.update(affected)


async def watch_updates(encoder):
    global disconnect, initial_fetch_completed
    while not disconnect:
        try:
            async with EventSource(f"{HN_URL}/updates.json",  timeout=-1) as client:
                async for event in client:
                    if disconnect:
                        break
                    updates = json.loads(event.data)
                    if updates:
                        buffer.append(updates)
                        log_with_timestamp(f"Buffer now at {len(buffer)}.")
                        if initial_fetch_completed:
                            await process_updates(buffer, encoder)
                            buffer.clear()
                            log_with_timestamp("Buffer cleared.")
        except aiohttp.client_exceptions.ClientConnectorError as e:
            print(f"Connection error: {e}, retrying in 5 seconds...")
            await asyncio.sleep(5)
        except TimeoutError:
            log_with_timestamp(
                "Connection to SSE channel timed out. Retrying in 5 seconds...")
            await asyncio.sleep(5)


def shutdown():
    global disconnect
    log_with_timestamp("Closing sync db...")
    conn.close()
    disconnect = True
    log_with_timestamp("Shutting down SSE channel...")


async def run(db_path, encoder):
    global conn, initial_fetch_completed
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    updates = asyncio.create_task(watch_updates(encoder))
    # embedding_task = asyncio.create_task(process_affected_stories(encoder))

    async with aiohttp.ClientSession() as session:
        # Fetch max item ID from Firebase and SQLite.
        print("Fetching max item ID for catching up...")
        max_item_id = get_max_item_id()
        max_item_id_from_db = get_max_item_id_from_db()
        start_id = max(max_item_id_from_db - OFFSET, 1)

        log_with_timestamp(
            f"Fetching items from ID {start_id} to {max_item_id}")
        await fetch_and_insert_items(session, start_id, max_item_id)
        log_with_timestamp(
            f"Finished initial fetch, now inserting updates (buffered {len(buffer)}).")
        initial_fetch_completed = True

    return updates
