# Hacker News 🤝 ChatGPT Plugin

This is a ChatGPT plugin to query, analyze, and summarize insights from the [Hacker News](https://news.ycombinator.com) community!

## Demo

If you have access to [ChatGPT plugins](https://openai.com/blog/chatgpt-plugins), just add this as an unverified plugin using the URL: https://hn.kix.in/

If you don't have plugins access, you can try out a [basic approximation of the experience here](https://hn.kix.in/). The full REST API exposed to ChatGPT is documented [here](https://hn.kix.in/docs), where you can also interact with it.

👉 [Video and detailed explanation of the code](https://www.kix.in/2023/05/05/hacker-news-chatgpt-plugin/) 👈

## Running locally

⬇️ [**Download the SQLite DB from**](https://huggingface.co/datasets/anantn/hacker-news/tree/main) 🤗

Assuming you have 32GB of RAM, take a look at [playground.ipynb](playground.ipynb) for quick and dirty ways to run analysis on the sqlite dataset loaded into memory.

You'll need atleast 30G of free disk space and >20G RAM to run the semantic search engine and ChatGPT plugin API. An NVIDIA GPU is highly recommended, embedding generation on CPU is painfully slow and untested on non-NVIDIA GPUs.

Clone the repo and install pre-requisites.

```bash
git clone https://github.com/anantn/hn-chatgpt-plugin.git
cd hn-chatgpt-plugin/api-server
pip install -r requirements.txt
cd ../embeddings
pip install -r requirements.txt

# Install zstd with your favorite package manager (brew, apt, etc)
sudo apt install zstd
```

Grab the datasets [from HuggingFace](https://huggingface.co/datasets/anantn/hacker-news/tree/main) and decompress them:

```bash
wget https://huggingface.co/datasets/anantn/hacker-news/resolve/main/hn-sqlite-20230429.db.zst
pzstd -d hn-sqlite-20230429.db.zst

wget https://huggingface.co/datasets/anantn/hacker-news/resolve/main/hn-sqlite-20230429_embeddings.db.zst
pzstd -d hn-sqlite-20230429_embeddings.db.zst
```

Run the embedding server first. The embedding server will by default try to "catch up" on all the latest data changes since the snapshot was generated. You can disable all data updates (recommended for your first run):

```bash
DB_PATH=hn-sqlite-20230429.db OPTS=nosync,noembedcu,noembedrt python main.py
```

If you want to generate embeddings and keep your local SQLite database up to date, just run `main.py` with no `OPTS` environment variable.

Once the embedding server is running, start the API server:

```bash
cd hn-chatgpt-plugin/api-server
DB_PATH=hn-sqlite-20230429.db python main.py
```

Fire up `localhost:8000` in your browser!

## Algolia Search Plugin

Earlier attempt, but still useful: integrates [Algolia's Hacker News search API](https://hn.algolia.com/api) with [ChatGPT plugins](https://openai.com/blog/chatgpt-plugins) to have conversations about content on hacker news.

If you have plugin access, you can try it:

```bash
$ cd algolia
$ pip install -r requirements.txt
$ python app.py
```

[Open a chat](https://chat.openai.com/) with plugins enabled, then: `Plugin store > Develop your own plugin > localhost:3333 > Fetch manifest`.

ChatGPT seems to hallucinate some parameters to the API, particularly the `sortBy` and `sortOrder` arguments &mdash; which may make sense to implement.

## Datasette Plugin

[Datasette](https://datasette.io/) exposes a REST API to any SQLite database. I [experimented with](datasette/) using it to interace with ChatGPT. You can try it in the same way as the algolia plugin.
