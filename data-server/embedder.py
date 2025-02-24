import os
import json
import collections
from utils import log
from openai import OpenAI, OpenAIError

client = OpenAI()
MAX_CACHE_SIZE = 100000
CACHE_FILE = "embedder_cache.jsonl"


class Embedder:
    def __init__(self):
        self.cache = collections.OrderedDict()
        self.cache_hits = 0
        self.load_cache()

    def load_cache(self):
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        self.cache[record["query"]] = record["embedding"]
            log(f"Loaded {len(self.cache)} cache entries from {CACHE_FILE}.")

    def encode(self, query):
        # Normalize
        query = " ".join(query.lower().split())

        # Check cache
        if query in self.cache:
            self.cache_hits += 1
            self.cache.move_to_end(query)
            return self.cache[query]

        # Perform request
        try:
            response = client.embeddings.create(
                input=query, model="text-embedding-3-small"
            )
            embeddings = response.data[0].embedding

            # Store in cache
            self.cache[query] = embeddings
            self.cache.move_to_end(query)
            if len(self.cache) > MAX_CACHE_SIZE:
                self.cache.popitem(last=False)
            return embeddings
        except OpenAIError as e:
            print(f"OpenAI Error: {e}")
            return None
