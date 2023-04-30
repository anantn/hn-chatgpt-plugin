import os
import gc
import sys
import time
import sqlite3
import numpy as np
import faiss
import psutil
from datetime import datetime
from InstructorEmbedding import INSTRUCTOR

TOP_K = 50
NLIST = 100
NPROBE = 35
start_time = time.time()


def print_memory_usage(phase):
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    elapsed = time.time() - start_time
    print(f"{elapsed:.2f}s Memory used ({phase}): {mem_info.rss / (1024 * 1024):.2f} MB")


def fetch_story_metadata(conn, story_id):
    cursor = conn.cursor()
    cursor.execute(
        "SELECT title, score, time FROM items WHERE id = ?", (story_id,))
    result = cursor.fetchone()
    cursor.close()
    return result


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


def normalize(values, reverse=False):
    min_val = min(values)
    max_val = max(values)
    normalized_values = [(value - min_val) / (max_val - min_val)
                         for value in values]
    if reverse:
        normalized_values = [1 - value for value in normalized_values]
    return normalized_values


def compute_rankings(results, query):
    scores, ages, distances = zip(
        *[(score, age, distance) for _, score, age, _, distance in results])
    normalized_scores = normalize(scores)
    normalized_ages = normalize(ages)
    normalized_distances = normalize(distances, reverse=True)

    w1, w2, w3, w4 = 0.4, 0.4, 0.1, 0.1

    rankings = []
    for i, (title, score, age, item_id, distance) in enumerate(results):
        query_words = set(word.lower() for word in query.split())
        title_words = set(word.lower() for word in title.split())
        matches = len(query_words.intersection(title_words))
        direct_match_boost = w4 * matches

        score_rank = w1 * \
            normalized_scores[i] + w2 * normalized_distances[i] + \
            w3 * normalized_ages[i] + direct_match_boost
        rankings.append((score_rank, title, score, age, item_id, distance))

    return sorted(rankings, reverse=True)


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
    index.nprobe = NPROBE
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
        D, I = index_with_ids.search(
            np.array([query_embedding]), TOP_K)
        end_time = time.time()
        print(f"FAISS search took {end_time - start_time:.2f} seconds")

        # Fetch metadata and do ranking
        story_metadata = [(title, score, age, story_id, l2_distance) for story_id, l2_distance in zip(
            I[0], D[0]) for title, score, age in [fetch_story_metadata(items_conn, int(story_id))]]
        ranked_stories = compute_rankings(story_metadata, query)

        # Print the titles of the ranked unique stories
        print(f"\nUnique stories for the query (from {TOP_K} selects):")
        unique_story_ids = set()
        for score_rank, title, score, age, story_id, distance in ranked_stories:
            if story_id not in unique_story_ids:
                unique_story_ids.add(story_id)
                submitted = datetime.fromtimestamp(age).strftime("%Y-%m-%d")
                print(
                    f"{score_rank:.3f} {score:4} {submitted} {distance:.2f} {story_id:10} {title}")


if __name__ == "__main__":
    main()
