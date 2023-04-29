import os
import gc
import time
import sqlite3
import numpy as np
import faiss
import psutil

TOP_K = 50
NLIST = 100
NPROBE = 20
INSTRUCTION = 'Represent the question for retrieving supporting forum discussions: '


class Index:
    def __init__(self, encoder, embeddings_db_path):
        self.encoder = encoder
        self.start_time = time.time()

        self.print_memory_usage("before loading embeddings")
        embeddings, item_ids = self.load_embeddings(embeddings_db_path)

        self.print_memory_usage("before flat_index")
        index = self.create_ivf_flat_index(embeddings)
        index.train(embeddings)
        index.nprobe = NPROBE
        self.print_memory_usage("after training")

        self.index_with_ids = faiss.IndexIDMap(index)
        self.index_with_ids.add_with_ids(embeddings, item_ids)
        embeddings = None
        gc.collect()
        self.print_memory_usage("vector index ready!")

    async def search(self, query, top_k=TOP_K):
        query_embedding = await self.embed_query(query)
        _, I = self.index_with_ids.search(np.array([query_embedding]), top_k)

        unique_story_ids = []
        seen_ids = set()
        for story_id in I[0]:
            if story_id not in seen_ids:
                seen_ids.add(story_id)
                unique_story_ids.append(story_id.item())
        return unique_story_ids

    async def embed_query(self, query):
        value = await self.encoder.encode([[INSTRUCTION, query]])
        return value[0]

    def load_embeddings(self, embeddings_db_path):
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
