import gc
import numpy as np
import faiss

from utils import log_with_mem


class Index:
    TOP_K = 50
    NLIST = 100
    NPROBE = 35
    EMBEDDING_DIM = 1536

    def __init__(self, embed_conn, encoder):
        self.encoder = encoder
        self.embed_conn = embed_conn

        embeddings, item_ids = self.load_embeddings()
        self.index = faiss.IndexIVFFlat(
            faiss.IndexFlatL2(self.EMBEDDING_DIM),
            self.EMBEDDING_DIM,
            self.NLIST,
            faiss.METRIC_L2,
        )
        log_with_mem("loaded embeddings into memory")

        self.index.train(embeddings)
        self.index.nprobe = self.NPROBE
        log_with_mem("trained index")

        self.index.add_with_ids(embeddings, item_ids)
        embeddings = None
        gc.collect()
        log_with_mem("built index with IDs")

    async def search(self, query, top_k=TOP_K):
        query_embedding = self.encoder.encode(query)
        if not query_embedding:
            return []
        D, I = self.index.search(np.array([query_embedding], dtype=np.float32), top_k)

        unique_story_ids = []
        seen_ids = set()
        for story_id, distance in zip(I[0], D[0]):
            if story_id not in seen_ids:
                seen_ids.add(story_id)
                unique_story_ids.append((story_id.item(), distance.item()))
        return unique_story_ids

    def update_embeddings(self, story_ids):
        # log_with_mem(f"updating {len(story_ids)} embeddings")
        for story_id in story_ids:
            self.index.remove_ids(np.array([story_id], dtype=np.int64))
            new_embeddings, new_item_ids = self.load_embeddings(
                f"WHERE story = {story_id}"
            )
            self.index.add_with_ids(new_embeddings, new_item_ids)
        # log_with_mem(f"updated faiss index!\n")

    def load_embeddings(self, constraint=""):
        cursor = self.embed_conn.cursor()

        # Fetch the total number of embeddings
        cursor.execute(f"SELECT COUNT(*) FROM embeddings {constraint}")
        num_embeddings = cursor.fetchone()[0]

        # Create an empty numpy array to hold the embeddings and item IDs
        embeddings = np.empty((num_embeddings, self.EMBEDDING_DIM), dtype=np.float32)
        item_ids = np.empty(num_embeddings, dtype=np.int64)

        # Fetch all embeddings and their story/item IDs from the database and fill the numpy arrays
        cursor.execute(
            f"SELECT story, embedding FROM embeddings {constraint} ORDER BY id"
        )
        for i, (story_id, embedding) in enumerate(cursor.fetchall()):
            item_ids[i] = story_id
            embeddings[i] = np.frombuffer(embedding, dtype=np.float32)

        cursor.close()
        return embeddings, item_ids
