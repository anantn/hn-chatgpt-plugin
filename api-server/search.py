import time
import requests

from sqlalchemy import and_
from sqlalchemy.sql import text
from sqlalchemy.orm import load_only

import utils
from schema import *


def search_results(
    session,
    ids,
    top_k,
    skip,
    limit,
    query,
    times,
    exclude_text=False,
    with_answer=False,
    suffix=None,
):
    expand = time.time()
    limit_ids = ids[skip : skip + limit]
    filtered = session.query(Item).filter(Item.id.in_(limit_ids))

    if exclude_text:
        filtered = filtered.options(
            load_only(
                *[
                    Item.id,
                    Item.type,
                    Item.by,
                    Item.time,
                    Item.url,
                    Item.score,
                    Item.title,
                    Item.descendants,
                ]
            )
        ).all()
    else:
        filtered = utils.with_top_comments(session, filtered.all())

    ordered_items = sorted(filtered, key=lambda item: limit_ids.index(item.id))
    expand = time.time() - expand
    times["fetch_time"] += expand

    log_msg = (
        f"search({times['search_time']:.3f}) "
        f"rank({times['rank_time']:.3f}) fetch({times['fetch_time']:.3f}) "
        f"num({top_k} -> {len(ids)} -> {len(ordered_items)}): "
        f"'{query}'"
    )
    if suffix:
        log_msg += f" {suffix}"
    print(log_msg)

    if with_answer:
        ordered_items = utils.with_answer(session, query, ordered_items)
    return ordered_items


def search(
    url,
    session,
    query,
    exclude_text,
    by,
    before_time,
    after_time,
    min_score,
    max_score,
    min_comments,
    max_comments,
    sort_by,
    sort_order,
    skip,
    limit,
    with_answer,
):
    # Build filters
    query_filters = []
    if by:
        query_filters.append(Item.by == by)
    if before_time:
        query_filters.append(Item.time <= before_time)
    if after_time:
        query_filters.append(Item.time >= after_time)
    if min_score:
        query_filters.append(Item.score >= min_score)
    if max_score:
        query_filters.append(Item.score <= max_score)
    if min_comments:
        query_filters.append(Item.descendants >= min_comments)
    if max_comments:
        query_filters.append(Item.descendants <= max_comments)

    # Perform semantic search
    top_k = 100
    if len(query_filters) > 0:
        top_k = 1000
    results = semantic_search(url, session, query, top_k=top_k)
    ids = [story_id for _, story_id in results["results"]]
    times = {
        "search_time": results["search_time"],
        "rank_time": results["rank_time"],
        "fetch_time": 0,
    }

    # See if we can early return
    if len(query_filters) == 0 and sort_by == SortBy.relevance:
        return search_results(
            session, ids, top_k, skip, limit, query, times, exclude_text, with_answer
        )

    # Apply filters if necessary
    times["fetch_time"] = time.time()
    query_filters.append(Item.id.in_(ids))
    filter_query = session.query(Item.id).filter(and_(*query_filters))

    # Sort results
    if sort_by != SortBy.relevance:
        sort_column = getattr(Item, sort_by.value)
        if sort_order == SortOrder.asc:
            filter_query = filter_query.order_by(sort_column.asc())
        elif sort_order == SortOrder.desc:
            filter_query = filter_query.order_by(sort_column.desc())

    filtered = filter_query.all()
    if sort_by == SortBy.relevance:
        filtered = sorted(filtered, key=lambda item: ids.index(item.id))
    times["fetch_time"] = time.time() - times["fetch_time"]
    filtered_ids = [item[0] for item in filtered]
    return search_results(
        session,
        filtered_ids,
        top_k,
        skip,
        limit,
        query,
        times,
        exclude_text,
        with_answer,
        suffix=f"filters({len(query_filters)})",
    )


def semantic_search(url, session, query, top_k=100):
    query = query.strip()

    # Perform semantic search
    start = time.time()
    req = requests.get(url, params={"query": query, "top_k": top_k})
    results = req.json()
    search_time = time.time() - start

    # Rank results
    start = time.time()
    results = compute_rankings(session, query, results)
    rank_time = time.time() - start

    return {
        "results": results,
        "search_time": search_time,
        "rank_time": rank_time,
    }


def normalize(values, reverse=False):
    min_val = min(values)
    max_val = max(values)
    if max_val == min_val:
        normalized_values = [0 if reverse else 1] * len(values)
    else:
        normalized_values = [
            (value - min_val) / (max_val - min_val) for value in values
        ]
        if reverse:
            normalized_values = [1 - value for value in normalized_values]
    return normalized_values


def compute_rankings(session, query, results):
    expanded = []
    for story_id, distance in results:
        cursor = session.execute(
            text(f"SELECT title, score, time FROM items WHERE id = {story_id}")
        ).cursor
        story_data = cursor.fetchone()
        if story_data is None:
            continue
        title, score, published = story_data[0], story_data[1], story_data[2]
        if title is None:
            continue
        score = 1 if score is None else score
        published = 0 if published is None else published
        expanded.append((story_id, distance, title, score, published))
        cursor.close()

    _, distances, _, scores, pub_times = zip(*expanded)
    normalized_scores = normalize(scores)
    normalized_distances = normalize(distances, reverse=True)

    now = time.time()
    recencies = [now - t for t in pub_times]
    normalized_recencies = normalize(recencies, reverse=True)

    w_score, w_dist, w_recency, w_topic = 0.25, 0.25, 0.4, 0.15

    def calculate_topicality(query_words, title_words):
        topicality = 0
        for i, title_word in enumerate(title_words):
            if title_word in query_words:
                # Boost based on position in the title
                topicality += 1 / (i + 1)
        return topicality

    rankings = []
    for i, (story_id, distance, title, _, _) in enumerate(expanded):
        query_words = set(word.lower() for word in query.split())
        title_words = [word.lower() for word in title.split()]
        topicality = calculate_topicality(query_words, title_words)

        score_rank = (
            w_score * normalized_scores[i]
            + w_dist * normalized_distances[i]
            + w_recency * normalized_recencies[i]
            + w_topic * topicality
        )
        rankings.append((score_rank, story_id))

    return sorted(rankings, reverse=True)
