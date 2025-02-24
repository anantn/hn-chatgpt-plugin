import json
from openai import OpenAI

client = OpenAI()

EXAMPLE_QUESTIONS = [
    "best laptop for coding that isn't from apple",
    "what acquisitions has mozilla made",
    "how can i land a job at faang?",
    "help me find true love",
    "what's it like working at an early stage startup",
    "top data science tools i should learn",
    "interesting articles about astronomy",
    "latest breakthroughs in battery technology",
    "how do i become a great manager?",
    "effective strategies for overcoming procrastination",
]

CACHE_FILE = "embedder_cache.jsonl"

with open(CACHE_FILE, "w", encoding="utf-8") as f:
    for query in EXAMPLE_QUESTIONS:
        # Normalize the query
        normalized_query = " ".join(query.lower().split())

        # Call the OpenAI embedding API
        response = client.embeddings.create(input=query, model="text-embedding-3-small")
        embeddings = response.data[0].embedding

        # Write the query and its embedding as a JSON object to the file
        record = {"query": normalized_query, "embedding": embeddings}
        f.write(json.dumps(record) + "\n")
        print(f"Cached embedding for query: '{normalized_query}'")
