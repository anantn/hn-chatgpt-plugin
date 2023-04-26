import os
import sys
import time
import sqlite3
import numpy as np
import faiss
import psutil
from InstructorEmbedding import INSTRUCTOR


def print_memory_usage(phase):
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    print(f"Memory used ({phase}): {mem_info.rss / (1024 * 1024):.2f} MB")


def fetch_story_title(conn, story_id):
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM items WHERE id = ?", (story_id,))
    title = cursor.fetchone()[0]
    cursor.close()
    return title


def load_embeddings(embeddings_db_path):
    embeddings_conn = sqlite3.connect(
        f"file:{embeddings_db_path}?mode=ro", uri=True)
    cursor = embeddings_conn.cursor()

    # Fetch the total number of embeddings
    cursor.execute("SELECT COUNT(*) FROM embeddings")
    num_embeddings = cursor.fetchone()[0]

    # Fetch the dimension of embeddings
    cursor.execute(
        "SELECT LENGTH(embedding) / 4 as dim FROM embeddings LIMIT 1")
    dim = cursor.fetchone()[0]

    # Create an empty numpy array to hold the embeddings and item IDs
    embeddings = np.empty((num_embeddings, dim), dtype=np.float32)
    item_ids = np.empty(num_embeddings, dtype=np.int64)

    # Fetch all embeddings and their story/item IDs from the database and fill the numpy arrays
    cursor.execute("SELECT story, embedding FROM embeddings ORDER BY id")
    for i, (story_id, embedding) in enumerate(cursor.fetchall()):
        item_ids[i] = story_id
        embeddings[i] = np.frombuffer(embedding, dtype=np.float32)

    cursor.close()
    embeddings_conn.close()
    return embeddings, item_ids, dim


def main():
    if len(sys.argv) < 2:
        print("Usage: python search.py <db_path>")
        sys.exit(1)

    db_path = sys.argv[1]
    expanded_db_path = os.path.expanduser(db_path)

    print_memory_usage("before loading")
    prefix = os.path.splitext(expanded_db_path)[0]
    embeddings_db_path = f"{prefix}_embeddings.db"
    embeddings, item_ids, dim = load_embeddings(embeddings_db_path)
    print_memory_usage("after loading")

    # Create a FAISS index and add the embeddings
    index = faiss.IndexFlatL2(dim)
    index_with_ids = faiss.IndexIDMap(index)
    index_with_ids.add_with_ids(embeddings, item_ids)
    print_memory_usage("after indexing")

    # Load instructor model
    model = INSTRUCTOR('hkunlp/instructor-large')
    print_memory_usage("after loading instructor-large")

    # Query loop
    items_conn = sqlite3.connect(f"file:{expanded_db_path}?mode=ro", uri=True)
    print("Enter your query, press Ctrl-D to exit.")
    while True:
        try:
            query = input("\nQuery: ")
        except EOFError:
            print("\nExiting...")
            break

        # Encode the query
        start_time = time.time()
        query_embedding = model.encode(
            [['Represent the question for retrieving supporting forum discussions: ', query]])[0]
        end_time = time.time()
        print(f"Embedding generation took {end_time - start_time:.2f} seconds")

        # Search the FAISS index
        k = 25
        start_time = time.time()
        distances, indices = index_with_ids.search(
            np.array([query_embedding]), k)
        end_time = time.time()
        print(f"FAISS search took {end_time - start_time:.2f} seconds")

        # Print the titles of the top 5 unique stories
        print("\nTop 5 unique stories for the query:")
        unique_story_ids = set()
        count = 0
        for story_id in indices[0]:
            if story_id not in unique_story_ids:
                unique_story_ids.add(story_id)
                print(fetch_story_title(items_conn, int(story_id)))
                count += 1
                if count == 5:
                    break


if __name__ == "__main__":
    main()
