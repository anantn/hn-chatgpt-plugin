import re
import html
import asyncio
import datetime

from tqdm import tqdm
from collections import defaultdict
from InstructorEmbedding import INSTRUCTOR

from utils import log, log_with_mem


class Embedder:
    def __init__(self):
        self.model = INSTRUCTOR("hkunlp/instructor-large")
        self.model.max_seq_length = DocumentEmbedder.TOKEN_LIMIT
        self.request_queue = asyncio.PriorityQueue()
        self._stop_event = asyncio.Event()
        self.processing_task = asyncio.create_task(self._process_requests())

    async def encode(self, text_pairs, high_priority=False):
        priority = 0 if high_priority else 1
        result_queue = asyncio.Queue()
        await self.request_queue.put((priority, (text_pairs, result_queue)))
        return await result_queue.get()

    async def _process_requests(self):
        while not self._stop_event.is_set():
            _, (text_pairs, result_queue) = await self.request_queue.get()
            if text_pairs is not None:
                embeddings = self.model.encode(text_pairs)
                await result_queue.put(embeddings)

    async def shutdown(self):
        self._stop_event.set()
        self.processing_task.cancel()
        try:
            await self.processing_task
        except asyncio.CancelledError:
            pass


class DocumentEmbedder:
    CHARACTER_LIMIT = 4096
    TOKEN_LIMIT = 1024
    BATCH_SIZE = 16
    INSTRUCTION = "Represent the forum discussion on a topic:"
    MIN_SCORE = 20
    MIN_DESCENDANTS = 3

    def __init__(self, db_conn, embed_conn, encoder):
        self.db_conn = db_conn
        self.embed_conn = embed_conn
        self.encoder = encoder

        # Create the embeddings table if it doesn't exist
        self.embed_conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                story INTEGER,
                part_index INTEGER,
                embedding BLOB,
                UNIQUE (story, part_index)
            )
        """)

    async def process_stories(self, story_ids):
        progress = tqdm(desc="parts processed")
        doc_progress = tqdm(desc="documents processed")

        processed = []
        cursor = self.db_conn.cursor()
        for story_id in story_ids:
            constraint = f"FROM items WHERE type = 'story' AND id = {story_id}"
            cursor.execute(f"SELECT score, descendants {constraint}")
            score, descendants = cursor.fetchone()
            score = 0 if not score else score
            descendants = 0 if not descendants else descendants
            if score < self.MIN_SCORE or descendants < self.MIN_DESCENDANTS:
                continue
            await self.process_stories_with_constraint(constraint, progress, doc_progress, batch_size=1)
            processed.append(story_id)

        progress.close()
        doc_progress.close()
        return processed

    async def process_catchup_stories(self, offset=0):
        # Fetch all interesting stories
        interesting = f"AND score >= {self.MIN_SCORE} AND descendants >= {self.MIN_DESCENDANTS}"
        constraint = f"FROM items WHERE type = 'story' {interesting}"

        # Fetch the last processed story
        last_processed_story = self.fetch_last_processed_story()

        # Return the difference between the two lists
        missing = self.find_missing(constraint)
        if len(missing) > 0:
            log(
                f"Found {len(missing)} missing stories, resetting last_processed_story ({last_processed_story})")
            last_processed_story = min(min(missing), last_processed_story)

        if last_processed_story:
            log(f"Found last processed story: {last_processed_story}")
            if offset != 0:
                cursor = self.db_conn.cursor()
                cursor.execute('''SELECT id FROM (
        SELECT id FROM items WHERE id < ? AND type='story' ORDER BY id DESC
        ) AS subquery LIMIT 1 OFFSET ?;''', (last_processed_story, offset-1))
                last_processed_story = cursor.fetchone()[0]
                cursor.close()
            log(
                f"Resuming from story {last_processed_story} (after offset: {offset})")
            constraint += f" AND id > {last_processed_story}"

        cursor = self.db_conn.cursor()
        cursor.execute(f"SELECT COUNT(*) {constraint}")
        total_stories = cursor.fetchone()[0]
        cursor.close()
        log(f"Found total eligible discussions: {total_stories}")
        progress = tqdm(desc="parts processed")
        doc_progress = tqdm(desc="documents processed", total=total_stories)

        await self.process_stories_with_constraint(constraint, progress, doc_progress)
        progress.close()
        doc_progress.close()

    async def process_stories_with_constraint(self, constraint, progress, doc_progress, batch_size=BATCH_SIZE):
        story_iter = self.story_generator(constraint)
        story_batch = []

        for (story, comments) in story_iter:
            document_parts = self.create_documents(story, comments)
            for part_index, document_part in enumerate(document_parts):
                story_batch.append(
                    (story["id"], part_index, document_part))
                if len(story_batch) == batch_size:
                    await self.process_batch(story_batch)
                    progress.update(len(story_batch))
                    story_batch = []
            doc_progress.update()

        # Process the remaining stories in the batch
        if story_batch:
            await self.process_batch(story_batch)

    async def process_batch(self, story_batch):
        embeddings_batch = await self.encoder.encode(
            [[self.INSTRUCTION, part] for _, _, part in story_batch]
        )
        insert_data = [
            (story_id, part_index, embeddings.tobytes())
            for (story_id, part_index, _), embeddings in zip(story_batch, embeddings_batch)
        ]
        cursor = self.embed_conn.cursor()
        cursor.executemany(
            """
            INSERT OR REPLACE INTO embeddings (story, part_index, embedding)
            VALUES (?, ?, ?)
        """,
            insert_data,
        )
        self.embed_conn.commit()
        cursor.close()

    def find_missing(self, constraint):
        # Fetch list of ids from conn (items) table
        items_cursor = self.db_conn.cursor()
        items_cursor.execute(f"SELECT COUNT(*) {constraint}")
        items_count = items_cursor.fetchone()[0]
        log_with_mem(f"Found {items_count} interesting stories from items")

        embeddings_cursor = self.embed_conn.cursor()
        embeddings_cursor.execute(
            "SELECT COUNT(DISTINCT story) FROM embeddings")
        embeddings_count = embeddings_cursor.fetchone()[0]
        log_with_mem(f"Found {embeddings_count} stories with embeddings")

        # If no difference, just return
        if items_count <= embeddings_count:
            items_cursor.close()
            embeddings_cursor.close()
            return set()

        # Find difference
        log_with_mem("Finding missing stories")
        items = set()
        items_cursor.execute(f"SELECT id {constraint}")
        for item in items_cursor.fetchall():
            items.add(item[0])
            item = items_cursor.fetchone()
        items_cursor.close()

        embeddings = set()
        embeddings_cursor.execute("SELECT DISTINCT story FROM embeddings")
        for embedding in embeddings_cursor.fetchall():
            embeddings.add(embedding[0])
        embeddings_cursor.close()

        return items-embeddings

    def format_date(self, unix_timestamp):
        dt = datetime.datetime.fromtimestamp(unix_timestamp)
        return dt.strftime("%Y-%m-%d")

    def story_generator(self, constraint):
        cursor = self.db_conn.cursor()
        cursor.execute(f"SELECT id, title, text, parent {constraint}")
        row = cursor.fetchone()
        while row:
            story = dict(row)
            comments = self.fetch_comment_data(story["id"])
            yield story, comments
            row = cursor.fetchone()
        cursor.close()

    def filter_comments(self, comments):
        # Filter out comments containing "[dead]" or "[flagged]"
        filtered_comments = [
            comment for comment in comments if "[dead]" not in comment["text"] and "[flagged]" not in comment["text"]
        ]
        return filtered_comments

    def fetch_comments(self, parent_id):
        cursor = self.db_conn.cursor()
        cursor.execute(
            """
            SELECT id, title, text, parent
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
        if story["title"] is None or story["title"] == "":
            if story["text"] is None or story["text"] == "":
                return None

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
        if current_document is None:
            return []

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
        cursor = self.embed_conn.cursor()
        cursor.execute("SELECT MAX(story) FROM embeddings")
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result else None
