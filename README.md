# ChatGPT 🤝 Hacker News

Answer any question based on the discussion corpus on Hacker News through ChatGPT!

## Dataset

As of early April 2023, Hacker News contained 35,663,259 items of content (story submissions, comments, and polls), and 859,467 unique users.

The data isn't that large, and can be fetched through the [Firebase API](https://github.com/HackerNews/API).

I tried a bunch of different methods to maximize download throughput: [python](hn-to-sqlite/python), [go](hn-to-sqlite/go), and [node.js](hn-to-sqlite/node). Ultimately node was the most robust and reliable (though not the fastest) mechanism. It's possible to parallelize the download process &mdash; which I did &mdash; and ended up merging the databases.

* [fetch.js](hn-to-sqlite/node/fetch.js) is the core download script.
* [run.sh](hn-to-sqlite/node/run.sh) is a quick-and-dirty user-script to parallelize the download on AWS EC2. Note the hard-coded number of machines.
* [fetch-users.js](hn-to-sqlite/node/fetch-users.js) is a script to fetch user data profiles, can be done on a single machine.
* [merge.py](hn-to-sqlite/python/merge.py) can be used to merge each partition into a single sqlite file.

The final output is a sqlite file that's ~25GB. It compressed down to ~5GB with zstd &mdash; [download it from HuggingFace](https://huggingface.co/datasets/anantn/hacker-news/tree/main). This db includes indexes on some common fields, if you want to reduce the size further you can always `DROP INDEX`.

Decompressing it should take less than a minute on a good computer with an SSD:

```bash
❯ time pzstd -kd hn-sqlite-20230420.db.zst
hn-sqlite-20230420.db.zst: 23664996352 bytes
real    0m38.103s
```

## Algolia Plugin

Earlier attempt, but still useful: integrates [Algolia's Hacker News search API](https://hn.algolia.com/api) with [ChatGPT plugins](https://openai.com/blog/chatgpt-plugins) to have conversations about content on hacker news.

If you have plugin access, you can try it:

```bash
$ cd algolia
$ pip install -r requirements.txt
$ python app.py
```

[Open a chat](https://chat.openai.com/) with plugins enabled, then: `Plugin store > Develop your own plugin > localhost:3333 > Fetch manifest`.

ChatGPT seems to hallucinate some parameters to the API, particularly the `sortBy` and `sortOrder` arguments &mdash; which may make sense to implement.