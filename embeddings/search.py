import os
import gc
import sys
import time
import sqlite3
import numpy as np
import faiss
import psutil
from InstructorEmbedding import INSTRUCTOR

TOP_K = 50
TOP_N = 10
NLIST = 100
start_time = time.time()


def print_memory_usage(phase):
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    elapsed = time.time() - start_time
    print(f"{elapsed:.2f}s Memory used ({phase}): {mem_info.rss / (1024 * 1024):.2f} MB")


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


def create_ivf_flat_index(embeddings):
    dim = embeddings.shape[1]
    quantizer = faiss.IndexFlatL2(dim)
    index = faiss.IndexIVFFlat(quantizer, dim, NLIST, faiss.METRIC_L2)
    return index


def embed_query(query, model):
    return model.encode(
        [['Represent the question for retrieving supporting forum discussions: ', query]])[0]


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
    print_memory_usage(f"after loading {len(embeddings)} embeddings")

    # Create a FAISS index and add the embeddings
    index = create_ivf_flat_index(embeddings)
    index.train(embeddings)
    index.nprobe = 20
    print_memory_usage("after training")
    index_with_ids = faiss.IndexIDMap(index)
    index_with_ids.add_with_ids(embeddings, item_ids)
    embeddings = None
    gc.collect()
    print_memory_usage("after indexing")

    # Load instructor model
    model = INSTRUCTOR('hkunlp/instructor-large')
    print_memory_usage("after loading instructor-large")
    # Warmup query
    embed_query("query", model)
    print_memory_usage("after warmup query")

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
        query_embedding = embed_query(query, model)
        end_time = time.time()
        print(f"Embedding generation took {end_time - start_time:.2f} seconds")

        # Search the FAISS index

        start_time = time.time()
        distances, indices = index_with_ids.search(
            np.array([query_embedding]), TOP_K)
        end_time = time.time()
        print(f"FAISS search took {end_time - start_time:.2f} seconds")

        # Print the titles of the top 5 unique stories
        print(f"\nTop {TOP_N} unique stories for the query:")
        unique_story_ids = set()
        count = 0
        for story_id, l2_distance in zip(indices[0], distances[0]):
            if story_id not in unique_story_ids:
                unique_story_ids.add(story_id)
                title = fetch_story_title(items_conn, int(story_id))
                print(f"{title} {story_id} ({l2_distance:.2f})")
                count += 1
                if count == TOP_N:
                    break


if __name__ == "__main__":
    main()
