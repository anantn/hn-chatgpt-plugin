import re
import os
import json
import html
import sqlite3
import datetime
import tiktoken

from collections import defaultdict, deque
from tqdm import tqdm


TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")


class DocumentEmbedder:
    TOKEN_LIMIT = 8000
    BATCH_SIZE = 16
    MIN_SCORE = 20
    MIN_DESCENDANTS = 3

    def __init__(self, db_conn):
        self.db_conn = db_conn

    def get_story_documents(self, constraint):
        story_batch = []
        story_iter = self.story_generator(constraint)
        for story, comments in story_iter:
            document_parts = self.create_documents_bfs(story, comments)
            for part_index, document_part in enumerate(document_parts):
                story_batch.append((story["id"], part_index, document_part))
        return story_batch

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
            comment
            for comment in comments
            if "[dead]" not in comment["text"] and "[flagged]" not in comment["text"]
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
        text = re.sub("<[^>]*>", " ", text)
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
        return header + "Discussion:\n"

    def create_documents_bfs(self, story, comments):
        document_parts = []
        header = self.story_header(story)
        if header is None:
            return []

        # Build a mapping from parent id to child comments.
        comments_by_parent = defaultdict(list)
        for comment in comments:
            comments_by_parent[comment["parent"]].append(comment)

        # Given a top-level comment, produce its breadth-first group.
        def bfs_group(top_comment):
            group = []  # list of (level, text)
            queue = deque()
            queue.append((0, top_comment))
            while queue:
                level, comm = queue.popleft()
                group.append((level, self.clean_text(comm["text"])))
                for child in comments_by_parent.get(comm["id"], []):
                    queue.append((level + 1, child))
            return group

        # Format a line given its level and text.
        def format_line(level, text):
            return ("\t" * level) + text + "\n"

        # Helper: get token length of a string.
        def token_len(s):
            return len(TIKTOKEN_ENC.encode(s, disallowed_special=()))

        current_document = header

        # Process each top-level comment (its parent is the story id)
        for top_comment in comments_by_parent.get(story["id"], []):
            group = bfs_group(top_comment)
            # First, try to append the entire group if it fits.
            group_text = "".join(format_line(level, text) for level, text in group)
            if token_len(current_document + group_text) <= self.TOKEN_LIMIT:
                current_document += group_text
                continue

            # Otherwise, we must add the group piece‐by‐piece.
            i = 0
            while i < len(group):
                line_level, line_text = group[i]
                line = format_line(line_level, line_text)
                # If adding this line would exceed the token limit…
                if token_len(current_document + line) > self.TOKEN_LIMIT:
                    # Flush current document (if it already has some comment lines)
                    if current_document != header:
                        document_parts.append(current_document)
                    # Start a new document part with the header.
                    current_document = header
                    # In a new document part the first line must be top‑level.
                    # If the line we want to add is not top‑level, re‑emit the group’s top‑level comment
                    # (which is group[0]) and then “rebase” the current line so that it is only one level deep.
                    if group[i][0] != 0:
                        # Add the top‑level comment.
                        top_line = format_line(0, group[0][1])
                        # Only add it if it fits.
                        if token_len(current_document + top_line) <= self.TOKEN_LIMIT:
                            current_document += top_line
                        # Rebase the current line to level 1.
                        line = format_line(1, line_text)
                    # (Now the current_document has a top‑level comment; try adding the line again.)
                    if token_len(current_document + line) > self.TOKEN_LIMIT:
                        # If it still doesn’t fit (e.g. the comment is huge) then we simply skip it.
                        i += 1
                        continue
                    else:
                        current_document += line
                        i += 1
                else:
                    # It fits; add the line and move on.
                    current_document += line
                    i += 1

        document_parts.append(current_document)
        return document_parts


DB_PATH = os.getenv("DB_PATH")
if __name__ == "__main__":
    if not DB_PATH:
        print("Set DB_PATH to path of hn-sqlite.db")
        exit()

    db_path = os.path.expanduser(DB_PATH)
    db_conn = sqlite3.connect(db_path)
    db_conn.row_factory = sqlite3.Row
    doc_embedder = DocumentEmbedder(db_conn)

    # Get total number of stories to process
    constraint = "FROM items WHERE type = 'story' AND score >= 20 AND descendants >= 3"
    cursor = db_conn.cursor()
    cursor.execute(f"SELECT COUNT(*) {constraint}")
    total_stories = cursor.fetchone()[0]
    cursor.close()
    print(f"Found total eligible discussions: {total_stories}")
    story_progress = tqdm(desc="stories processed", total=total_stories)

    # Configuration limits.
    MAX_LINES = 50000
    MAX_FILE_SIZE = 190 * 1024 * 1024  # 190 MB in bytes
    file_index = 1
    lines_written = 0
    current_file_size = 0
    output_file_name = f"batch_{file_index}.jsonl"
    batch_file = open(output_file_name, "w", encoding="utf-8")

    # Get IDs of stories to process
    cursor = db_conn.cursor()
    cursor.execute(f"SELECT id {constraint}")
    total_tokens = 0
    while True:
        row = cursor.fetchone()
        if not row:
            break

        story_docs = doc_embedder.get_story_documents(f"FROM items WHERE id = {row[0]}")
        story_progress.update()
        for story_id, part_index, document_text in story_docs:
            cur_tokens = len(TIKTOKEN_ENC.encode(document_text, disallowed_special=()))
            total_tokens += cur_tokens
            record = {
                "custom_id": f"{story_id}-{part_index}",
                "tokens": cur_tokens,
                "method": "POST",
                "url": "/v1/embeddings",
                "body": {"model": "text-embedding-3-small", "input": document_text},
            }

            # Serialize record to a JSON string (with a newline).
            record_line = json.dumps(record) + "\n"
            # Compute the byte size of the record.
            record_line_size = len(record_line.encode("utf-8"))

            # If writing this record would exceed either limit, close the current file and open a new one.
            if (
                lines_written + 1 > MAX_LINES
                or current_file_size + record_line_size > MAX_FILE_SIZE
            ):
                batch_file.close()
                file_index += 1
                output_file_name = f"batch_{file_index}.jsonl"
                batch_file = open(output_file_name, "w", encoding="utf-8")
                lines_written = 0
                current_file_size = 0
                print(f"Tokens written so far: {total_tokens}")

            batch_file.write(record_line)
            lines_written += 1
            current_file_size += record_line_size

    print(f"Total tokens: {total_tokens}")
    story_progress.close()
    cursor.close()
