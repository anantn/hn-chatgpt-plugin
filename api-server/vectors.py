import os
import gc
import time
import sqlite3
import numpy as np
import faiss
import psutil

TOP_K = 50
NLIST = 100
NPROBE = 35
EMBEDDING_DIM = 768
INSTRUCTION = 'Represent the question for retrieving supporting forum discussions: '


class Index:
    def __init__(self, encoder, embeddings_db_path):
        self.encoder = encoder
        self.start_time = time.time()

        self.print_memory_usage("before loading embeddings")
        self.embeddings_conn = sqlite3.connect(
            f"file:{embeddings_db_path}?mode=ro", uri=True)
        embeddings, item_ids = self.load_embeddings()

        self.print_memory_usage("before flat_index")
        self.index = self.create_ivf_flat_index(embeddings)
        self.index.train(embeddings)
        self.index.nprobe = NPROBE
        self.print_memory_usage("after training")

        self.index.add_with_ids(embeddings, item_ids)
        embeddings = None
        gc.collect()
        self.print_memory_usage("vector index ready!")

    def shutdown(self):
        self.embeddings_conn.close()

    async def search(self, query, top_k=TOP_K):
        query_embedding = await self.embed_query(query)
        D, I = self.index.search(np.array([query_embedding]), top_k)

        unique_story_ids = []
        seen_ids = set()
        for story_id, distance in zip(I[0], D[0]):
            if story_id not in seen_ids:
                seen_ids.add(story_id)
                unique_story_ids.append((story_id.item(), distance.item()))
        return unique_story_ids

    async def embed_query(self, query):
        value = await self.encoder.encode([[INSTRUCTION, query]], high_priority=True)
        return value[0]

    def update_embeddings(self, story_ids):
        self.print_memory_usage(f"updating {len(story_ids)} embeddings")
        for story_id in story_ids:
            self.index.remove_ids(
                np.array([story_id], dtype=np.int64))
            new_embeddings, new_item_ids = self.load_embeddings(
                f"WHERE story = {story_id}")
            self.index.add_with_ids(new_embeddings, new_item_ids)
        self.print_memory_usage(f"finished embeddings index update!")

    def load_embeddings(self, constraint=""):
        cursor = self.embeddings_conn.cursor()

        # Fetch the total number of embeddings
        cursor.execute(f"SELECT COUNT(*) FROM embeddings {constraint}")
        num_embeddings = cursor.fetchone()[0]

        # Create an empty numpy array to hold the embeddings and item IDs
        embeddings = np.empty(
            (num_embeddings, EMBEDDING_DIM), dtype=np.float32)
        item_ids = np.empty(num_embeddings, dtype=np.int64)

        # Fetch all embeddings and their story/item IDs from the database and fill the numpy arrays
        cursor.execute(
            f"SELECT story, embedding FROM embeddings {constraint} ORDER BY id")
        for i, (story_id, embedding) in enumerate(cursor.fetchall()):
            item_ids[i] = story_id
            embeddings[i] = np.frombuffer(embedding, dtype=np.float32)

        cursor.close()
        return embeddings, item_ids

    def create_ivf_flat_index(self, embeddings):
        dim = embeddings.shape[1]
        quantizer = faiss.IndexFlatL2(dim)
        index = faiss.IndexIVFFlat(quantizer, dim, NLIST, faiss.METRIC_L2)
        return index

    def print_memory_usage(self, phase):
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        elapsed = time.time() - self.start_time
        print(
            f"{elapsed:.2f}s Memory used ({phase}): {mem_info.rss / (1024 * 1024):.2f} MB")
