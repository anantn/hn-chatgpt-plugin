import os
import re
import html
import asyncio
import sqlite3
import datetime

from tqdm import tqdm
from collections import defaultdict
from InstructorEmbedding import INSTRUCTOR


class Embedder:
    def __init__(self, model_name='hkunlp/instructor-large', max_seq_length=1024):
        self.model = INSTRUCTOR(model_name)
        self.model.max_seq_length = max_seq_length
        self.request_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()

    async def encode(self, text_pairs):
        result_queue = asyncio.Queue()
        await self.request_queue.put((text_pairs, result_queue))
        return await result_queue.get()

    async def _process_requests(self):
        while not self._stop_event.is_set():
            text_pairs, result_queue = await self.request_queue.get()
            embeddings = self.model.encode(text_pairs)
            await result_queue.put(embeddings)

    async def shutdown(self):
        self._stop_event.set()
        await self.request_queue.join()


class DocumentEmbedder:
    CHARACTER_LIMIT = 4096
    TOKEN_LIMIT = 1024
    BATCH_SIZE = 16
    INSTRUCTION = "Represent the forum discussion on a topic:"

    def __init__(self, db_path, model):
        self.model = model
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

        prefix = os.path.splitext(db_path)[0]
        self.embeddings_conn = sqlite3.connect(f"{prefix}_embeddings.db")

        # Create the embeddings table if it doesn't exist
        self.embeddings_conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                story INTEGER,
                part_index INTEGER,
                embedding BLOB,
                UNIQUE (story, part_index)
            )
        """)

    async def process_stories(self, story_ids):
        story_ids_str = ', '.join(map(str, story_ids))
        constraint = f"FROM items WHERE type = 'story' AND id IN ({story_ids_str})"
        await self.process_stories_with_constraint(constraint)

    async def process_catchup_stories(self, offset=0):
        # Fetch all interesting stories
        constraint = "FROM items WHERE type = 'story' AND score >= 20 AND descendants >= 3"

        # Fetch list of ids from conn (items) table
        items_cursor = self.conn.cursor()
        items_cursor.execute(f"SELECT id {constraint}")
        items = set()
        for item in items_cursor.fetchall():
            items.add(item[0])
        items_cursor.close()

        # Then fetch list of ids from embeddings_conn (embeddings) table
        embeddings_cursor = self.embeddings_conn.cursor()
        embeddings_cursor.execute("SELECT DISTINCT story FROM embeddings")
        embeddings = set()
        for embedding in embeddings_cursor.fetchall():
            embeddings.add(embedding[0])
        embeddings_cursor.close()

        # Return the difference between the two lists
        missing = items-embeddings
        if len(missing) > 0:
            print(
                f"Found {len(missing)} missing stories, resetting last_processed_story ({last_processed_story})")
            last_processed_story = min(missing)

        # Fetch the last processed story
        last_processed_story = self.fetch_last_processed_story()
        if last_processed_story:
            print("Found last processed story: ", last_processed_story)
            if offset != 0:
                print(f"Finding story with the right offset: {offset}")
                cursor = self.conn.cursor()
                cursor.execute('''SELECT id FROM (
        SELECT id FROM items WHERE id < ? AND type='story' ORDER BY id DESC
        ) AS subquery LIMIT 1 OFFSET ?;''', (last_processed_story, offset-1))
                last_processed_story = cursor.fetchone()[0]
                cursor.close()
            print(f"Resuming from story {last_processed_story}")
            constraint += f" AND id > {last_processed_story}"
        await self.process_stories_with_constraint(constraint)

    async def process_stories_with_constraint(self, constraint):
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT COUNT(*) {constraint}")
        total_stories = cursor.fetchone()[0]
        cursor.close()
        print(f"Found total eligible discussions: {total_stories}")

        progress = tqdm(desc="parts processed")
        doc_progress = tqdm(desc="documents processed", total=total_stories)

        story_iter = self.story_generator(constraint)
        story_batch = []

        for (story, comments) in story_iter:
            document_parts = self.create_documents(story, comments)
            for part_index, document_part in enumerate(document_parts):
                story_batch.append(
                    (story["id"], part_index, document_part))
                if len(story_batch) == self.BATCH_SIZE:
                    await self.process_batch(story_batch)
                    story_batch = []
                    progress.update(self.BATCH_SIZE)
            doc_progress.update()

        # Process the remaining stories in the batch
        if story_batch:
            await self.process_batch(story_batch)

        progress.close()
        doc_progress.close()

    async def process_batch(self, story_batch):
        embeddings_batch = await self.model.encode(
            [[self.INSTRUCTION, part] for _, _, part in story_batch]
        )
        insert_data = [
            (story_id, part_index, embeddings.tobytes())
            for (story_id, part_index, _), embeddings in zip(story_batch, embeddings_batch)
        ]
        cursor = self.embeddings_conn.cursor()
        cursor.executemany(
            """
            INSERT OR REPLACE INTO embeddings (story, part_index, embedding)
            VALUES (?, ?, ?)
        """,
            insert_data,
        )
        self.embeddings_conn.commit()
        cursor.close()

    def format_date(self, unix_timestamp):
        dt = datetime.datetime.fromtimestamp(unix_timestamp)
        return dt.strftime("%Y-%m-%d")

    def story_generator(self, constraint):
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT * {constraint}")

        for row in cursor:
            story = dict(row)
            comments = self.fetch_comment_data(story["id"])
            yield story, comments

        cursor.close()

    def filter_comments(self, comments):
        # Filter out comments containing "[dead]" or "[flagged]"
        filtered_comments = [
            comment for comment in comments if "[dead]" not in comment["text"] and "[flagged]" not in comment["text"]
        ]
        return filtered_comments

    def fetch_comments(self, parent_id):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM items
            WHERE type = 'comment' AND parent = ? AND text IS NOT NULL
            """,
            (parent_id,),
        )
        return cursor.fetchall()

    def fetch_comment_data(self, story_id):
        top_level_comments = self.fetch_comments(story_id)
        all_comments = []

        def fetch_descendants(parent_comment):
            child_comments = self.fetch_comments(parent_comment["id"])
            for child_comment in child_comments:
                all_comments.append(child_comment)
                fetch_descendants(child_comment)

        for top_level_comment in top_level_comments:
            all_comments.append(top_level_comment)
            fetch_descendants(top_level_comment)

        return self.filter_comments(all_comments)

    def clean_text(self, text):
        if text is None:
            return ""
        text = re.sub("<[^>]*>", "", text)
        text = re.sub("\r\n", "\n", text)
        text = html.unescape(text)
        return text

    def story_header(self, story):
        header = f'Topic: {self.clean_text(story["title"])}\n'
        # submitted by {clean_text(story["by"])} on {format_date(story["time"])}\n
        if story["text"]:
            header += f'{self.clean_text(story["text"])}\n'
        return header + 'Discussion:\n'

    def create_documents(self, story, comments):
        document_parts = []

        # Prepare a dictionary for comments, indexed by their parent ID
        comments_by_parent = defaultdict(list)
        for comment in comments:
            comments_by_parent[comment["parent"]].append(comment)

        current_document = self.story_header(story)

        # Iterate through top-level comments and their children
        stack = [(0, comment) for comment in comments_by_parent[story["id"]]]
        while stack:
            level, comment = stack.pop()

            # If adding the comment exceeds the character limit, start a new document
            if len(current_document) + len(comment["text"]) > self.CHARACTER_LIMIT:
                document_parts.append(current_document)
                current_document = self.story_header(story)

            current_document += "\t" * level + \
                f'{self.clean_text(comment["text"])}\n'

            # Add child comments to the stack
            for child_comment in comments_by_parent[comment["id"]]:
                stack.append((level + 1, child_comment))

        document_parts.append(current_document)
        return document_parts

    def fetch_last_processed_story(self):
        cursor = self.embeddings_conn.cursor()
        cursor.execute("SELECT MAX(story) FROM embeddings")
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result else None
