import os
import re
import sys
import html
import sqlite3
import datetime
from collections import defaultdict
from tqdm import tqdm
from InstructorEmbedding import INSTRUCTOR

# Approximate limits
CHARACTER_LIMIT = 4096
TOKEN_LIMIT = 1024
BATCH_SIZE = 16


def format_date(unix_timestamp):
    dt = datetime.datetime.fromtimestamp(unix_timestamp)
    return dt.strftime("%Y-%m-%d")


def story_generator(conn, constraint):
    cursor = conn.cursor()
    cursor.execute(f"SELECT * {constraint}")

    for row in cursor:
        story = dict(row)
        comments = fetch_comment_data(conn, story["id"])
        yield story, comments

    cursor.close()


def fetch_comments(conn, parent_id):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM items
        WHERE parent = ? AND type = 'comment' AND text IS NOT NULL
        """,
        (parent_id,),
    )
    return cursor.fetchall()


def fetch_comment_data(conn, story_id):
    top_level_comments = fetch_comments(conn, story_id)
    all_comments = []

    def fetch_descendants(parent_comment):
        child_comments = fetch_comments(conn, parent_comment["id"])
        for child_comment in child_comments:
            all_comments.append(child_comment)
            fetch_descendants(child_comment)

    for top_level_comment in top_level_comments:
        all_comments.append(top_level_comment)
        fetch_descendants(top_level_comment)

    # Filter out comments containing "[dead]" or "[flagged]"
    filtered_comments = [
        comment for comment in all_comments if "[dead]" not in comment["text"] and "[flagged]" not in comment["text"]
    ]

    return filtered_comments


def clean_text(text):
    text = re.sub("<[^>]*>", "", text)
    text = re.sub("\r\n", "\n", text)
    text = html.unescape(text)
    return text


def create_documents(conn, story, comments):
    def story_header(story):
        header = f'Topic: {clean_text(story["title"])}\n'
        # submitted by {clean_text(story["by"])} on {format_date(story["time"])}\n
        if story["text"]:
            header += f'{clean_text(story["text"])}\n'
        return header + 'Discussion:\n'

    document_parts = []

    # Prepare a dictionary for comments, indexed by their parent ID
    comments_by_parent = defaultdict(list)
    for comment in comments:
        comments_by_parent[comment["parent"]].append(comment)

    current_document = story_header(story)

    # Iterate through top-level comments and their children
    stack = [(0, comment) for comment in comments_by_parent[story["id"]]]
    while stack:
        level, comment = stack.pop()

        # If adding the comment exceeds the character limit, start a new document
        if len(current_document) + len(comment["text"]) > CHARACTER_LIMIT:
            document_parts.append(current_document)
            current_document = story_header(story)

        current_document += "\t" * level + f'{clean_text(comment["text"])}\n'

        # Add child comments to the stack
        for child_comment in comments_by_parent[comment["id"]]:
            stack.append((level + 1, child_comment))

    document_parts.append(current_document)

    return document_parts


def process_batch(embeddings_conn, model, story_batch):
    embeddings_batch = model.encode(
        [[instruction, part] for _, _, instruction, part in story_batch])

    insert_data = [(story_id, part_index, embeddings.tobytes())
                   for (story_id, part_index, _, _), embeddings in zip(story_batch, embeddings_batch)]

    cursor = embeddings_conn.cursor()
    cursor.executemany("""
        INSERT OR REPLACE INTO embeddings (story, part_index, embedding)
        VALUES (?, ?, ?)
    """, insert_data)
    embeddings_conn.commit()
    cursor.close()


def fetch_last_processed_story(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(story) FROM embeddings")
    result = cursor.fetchone()
    cursor.close()
    return result[0] if result else None


def main():
    if len(sys.argv) < 2:
        print("Usage: python embed.py <db_path> <optional: offset>")
        sys.exit(1)

    offset = 0
    if len(sys.argv) == 3:
        offset = int(sys.argv[2])

    db_path = sys.argv[1]
    expanded_db_path = os.path.expanduser(db_path)
    conn = sqlite3.connect(f"file:{expanded_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    prefix = os.path.splitext(expanded_db_path)[0]
    embeddings_db_path = f"{prefix}_embeddings.db"
    embeddings_conn = sqlite3.connect(embeddings_db_path)

    # Create the embeddings table if it doesn't exist
    embeddings_conn.execute("""
       CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story INTEGER,
            part_index INTEGER,
            embedding BLOB,
            UNIQUE (story, part_index)
        )
    """)

    # Fetch all interesting stories
    constraint = "FROM items WHERE type = 'story' AND score >= 20 AND descendants >= 3"

    # Fetch the last processed story
    last_processed_story = fetch_last_processed_story(embeddings_conn)
    if last_processed_story:
        print("Found last processed story: ", last_processed_story)
        if offset != 0:
            print(f"Finding story with the right offset: {offset}")
            cursor = conn.cursor()
            cursor.execute('''SELECT id FROM (
    SELECT id FROM items WHERE id < ? AND type='story' ORDER BY id DESC
    ) AS subquery LIMIT 1 OFFSET ?;''', (last_processed_story, offset-1))
            last_processed_story = cursor.fetchone()[0]
            cursor.close()
        print(f"Resuming from story {last_processed_story}")
        constraint += f" AND id > {last_processed_story}"

    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) {constraint}")
    total_stories = cursor.fetchone()[0]
    cursor.close()
    print(f"Found total eligible discussions: {total_stories}")

    # Generate embeddings
    model = INSTRUCTOR('hkunlp/instructor-large')
    model.max_seq_length = TOKEN_LIMIT
    instruction = "Represent the forum discussion on a topic:"
    progress = tqdm(desc="parts processed")
    doc_progress = tqdm(desc="documents processed", total=total_stories)

    story_iter = story_generator(conn, constraint)
    story_batch = []

    for (story, comments) in story_iter:
        document_parts = create_documents(conn, story, comments)
        for part_index, document_part in enumerate(document_parts):
            story_batch.append(
                (story["id"], part_index, instruction, document_part))
            if len(story_batch) == BATCH_SIZE:
                process_batch(embeddings_conn, model, story_batch)
                story_batch = []
                progress.update(BATCH_SIZE)
        doc_progress.update()

    # Process the remaining stories in the batch
    if story_batch:
        process_batch(embeddings_conn, model, story_batch)

    progress.close()
    doc_progress.close()
    conn.close()
    embeddings_conn.close()


if __name__ == "__main__":
    main()
