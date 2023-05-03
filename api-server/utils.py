import time
import copy
import asyncio
import requests

from sqlalchemy import case, and_
from sqlalchemy.sql import text
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from schema import *

# Helper methods

DEFAULT_NUM = 10
MAX_NUM = 50
TIMEOUT = 30  # seconds


def initialize_middleware(app):
    class TimeoutMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            async def call_next_with_request():
                return await call_next(request)
            task = asyncio.create_task(call_next_with_request())
            try:
                response = await asyncio.wait_for(task, timeout=10)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                response = JSONResponse(status_code=408, content={
                                        "detail": "Request timeout"})
            return response

    def set_schema():
        if app.openapi_schema:
            return app.openapi_schema
        app.openapi_schema = get_schema(app)
        return app.openapi_schema

    app.openapi = set_schema
    app.add_middleware(TimeoutMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8000",
            "https://chat.openai.com",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


def search(url, session, query, by,
           before_time, after_time, min_score, max_score,
           min_comments, max_comments, sort_by, sort_order,
           skip, limit):
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
    top_k = 50
    if len(query_filters) > 0:
        top_k = 1000
    results = semantic_search(url, session, query, top_k=top_k)
    ids = [story_id for _, story_id in results["results"]]
    sort_order_expr = case(
        {id_: idx for idx, id_ in enumerate(ids)}, value=Item.id)

    log_msg = f"search({results['search_time']:.3f}) " \
        f"rank({results['rank_time']:.3f}) " \
        f"num({top_k} -> {len(results['results'])} -> {limit}): " \
        f"'{query}'"

    # See if we can early return
    if len(query_filters) == 0 and sort_by == SortBy.relevance:
        print(log_msg)
        q = session.query(Item).filter(
            Item.id.in_(ids)).order_by(sort_order_expr)
        return with_summary(session, q.offset(skip).limit(limit).all())

    # Apply filters if necessary
    query_filters.append(Item.id.in_(ids))
    filtered_items = session.query(Item).filter(and_(*query_filters))

    # Sort results
    if sort_by == SortBy.relevance:
        filtered_items = filtered_items.order_by(sort_order_expr)
    else:
        sort_column = getattr(Item, sort_by.value)
        if sort_order == SortOrder.asc:
            filtered_items = filtered_items.order_by(sort_column.asc())
        elif sort_order == SortOrder.desc:
            filtered_items = filtered_items.order_by(sort_column.desc())

    print(f"{log_msg} -> {sort_by}/{len(query_filters)}")
    return with_summary(session, filtered_items.offset(skip).limit(limit).all())


def semantic_search(url, session, query, top_k=50):
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
        normalized_values = [(value - min_val) / (max_val - min_val)
                             for value in values]
        if reverse:
            normalized_values = [1 - value for value in normalized_values]
    return normalized_values


def compute_rankings(session, query, results):
    expanded = []
    for (story_id, distance) in results:
        cursor = session.execute(
            text(f"SELECT title, score, time FROM items WHERE id = {story_id}")).cursor
        title, score, age = cursor.fetchone()
        if title is None:
            continue
        score = 1 if score is None else score
        age = 0 if age is None else age
        expanded.append((story_id, distance, title, score, age))
        cursor.close()

    scores, ages, distances = zip(
        *[(score, age, distance) for _, distance, _, score, age in expanded])
    normalized_scores = normalize(scores)
    normalized_ages = normalize(ages)
    normalized_distances = normalize(distances, reverse=True)

    w1, w2, w3, w4, w5 = 0.25, 0.35, 0.1, 0.15, 0.15

    def calculate_topicality(query_words, title_words):
        topicality = 0
        for i, title_word in enumerate(title_words):
            if title_word in query_words:
                # Boost based on position in the title
                topicality += (1 / (i + 1))
        return topicality

    rankings = []
    for i, (story_id, distance, title, _, _) in enumerate(expanded):
        query_words = set(word.lower() for word in query.split())
        title_words = [word.lower() for word in title.split()]
        matches = len(query_words.intersection(set(title_words)))

        topicality = calculate_topicality(query_words, title_words)

        score_rank = w1 * normalized_scores[i] \
            + w2 * normalized_distances[i] \
            + w3 * normalized_ages[i] \
            + w4 * matches \
            + w5 * topicality
        rankings.append((score_rank, story_id))

    return sorted(rankings, reverse=True)


def with_summary(session, items):
    for item in items:
        item.summary = get_comments_text(session, item.id)
    return items


# Top 5 kid comments, and first child comment of each from the database
# TODO: limit to word count instead of comment count and find smarter way to rank
def get_comments_text(session, story_id):
    comment_text = []
    cursor = session.execute(text(f"""SELECT i.* FROM items i
                    JOIN kids k ON i.id = k.kid
                    WHERE k.item = {story_id} AND i.type = 'comment'
                    ORDER BY k.display_order
                    LIMIT 5""")).cursor
    column_names = [desc[0] for desc in cursor.description]
    comments = [Item(**dict(zip(column_names, row)))
                for row in cursor.fetchall()]
    for comment in comments:
        if comment.text:
            comment_text.append(comment.text)
            cursor = session.execute(text(f"""SELECT i.* FROM items i
                            JOIN kids k ON i.id = k.kid
                            WHERE k.item = {comment.id} AND i.type = 'comment'
                            ORDER BY k.display_order
                            LIMIT 1""")).cursor
            child_row = cursor.fetchone()
            if child_row:
                child_comment = Item(**dict(zip(column_names, child_row)))
                if child_comment.text:
                    comment_text.append(child_comment.text)
    return comment_text


# Populate parts with the poll responses
def get_poll_responses(session, items):
    poll_responses = []
    for item in items:
        working_item = copy.copy(item)
        if working_item.parts is not None:
            item_parts = [int(part_id)
                          for part_id in working_item.parts.split(",")]
            working_item.parts = None
            item_pollopts = session.query(Item.id, Item.type, Item.text, Item.score).filter(
                Item.id.in_(item_parts)).all()
            parts = []
            for pollopt in item_pollopts:
                if pollopt.text and pollopt.score:
                    parts.append(
                        {"text": pollopt.text, "score": pollopt.score})
        else:
            parts = []
        poll_response = ItemResponse.from_orm(working_item)
        poll_response.parts = parts
        poll_responses.append(poll_response)
    return poll_responses
