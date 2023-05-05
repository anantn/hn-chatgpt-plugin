# Hacker News 🤝 ChatGPT Plugin

This is a ChatGPT plugin to query, analyze, and summarize insights from the [Hacker News](https://news.ycombinator.com) community!

## Demo

If you have access to [ChatGPT plugins](https://openai.com/blog/chatgpt-plugins), just add this as an unverified plugin using the URL: https://hn.kix.in/

If you don't have plugins access, you can try out the basic [semantic search demo](https://hn.kix.in/). Here is a video of the plugin experience:

https://user-images.githubusercontent.com/37190/236521903-da8eb5a6-3b8e-4125-a8c0-64b869d47f55.mp4

The full REST API exposed to ChatGPT is documented [here](https://hn.kix.in/docs), where you can also interact with it.

## How does it work?

The REST API is described in this OpenAPI specification. This API is read by ChatGPT which then translates incoming queries to equivalent API calls when appropriate. Read more about this process in the [documentation for ChatGPT plugins](https://platform.openai.com/docs/plugins/introduction).

The API offeres basic metadata retrieval over stories, comments, and users in the HN corpus, but notably offers access to a semantic search index. Semantic search looks for content that is similar in meaning to the query and is more naturally suited to find content matching natural language input.

Semantic search is powered by embeddings. Embeddings are a way to construct a vector representation from input data, such that similar data are closer together in an n-dimensional space.

Once the HN corpus was downloaded into SQLite (see the section below on how the dataset was created), the semantic search index was created by [generating embeddings](embeddings/embed.py) for stories and comments. These embeddings are then loaded into memory and indexed using [Faiss](https://github.com/facebookresearch/faiss/).

The [embeddings server](embeddings/main.py) keeps the data updated through the [HN Firebase API](https://github.com/HackerNews/API), and also regenerates and updates the embeddings index periodically. The [API server](api-server/main.py) exposes most of the basic functionality you'd expect from a wrapper on a database, and is the interface ChatGPT interacts with. Notably, the API server supports semantic search through use of the Faiss embeddings index.

This allows ChatGPT to find the right content to analyze and summarize that feels more natural in conversation.

## Running locally

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

## Dataset

As of early April 2023, Hacker News contained 35,663,259 items of content (story submissions, comments, and polls), and 859,467 unique users.

The data isn't that large, and can be fetched through the [Firebase API](https://github.com/HackerNews/API).

👉 [**Download the SQLite DB from 🤗**](https://huggingface.co/datasets/anantn/hacker-news/tree/main)

I tried a bunch of different methods to maximize download throughput: [python](hn-to-sqlite/python), [go](hn-to-sqlite/go), and [node.js](hn-to-sqlite/node). Ultimately node was the most robust and reliable (though not the fastest) mechanism. It's possible to parallelize the download process &mdash; which I did &mdash; and ended up merging the databases.

* [fetch.js](hn-to-sqlite/node/fetch.js) is the core download script.
* [run.sh](hn-to-sqlite/node/run.sh) is a quick-and-dirty user-script to parallelize the download on AWS EC2. Note the hard-coded number of machines.
* [fetch-users.js](hn-to-sqlite/node/fetch-users.js) is a script to fetch user data profiles, can be done on a single machine.
* [merge.py](hn-to-sqlite/python/merge.py) can be used to merge each partition into a single sqlite file.

The final output is a sqlite file that's ~30GB. It compressed down to ~6GB with zstd which is the version hosted on HuggingFace. This includes indexes on some common fields, if you want to reduce the size further you can always `DROP INDEX`.

Decompressing it should take less than a minute on a good computer with an SSD:

```bash
❯ time pzstd -kd hn-sqlite-20230420.db.zst
hn-sqlite-20230420.db.zst: 23664996352 bytes
real    0m38.103s
```

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
