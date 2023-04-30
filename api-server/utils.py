import time

from sqlalchemy import or_
from sqlalchemy.orm import noload
from typing import Optional, Union

import dbsync
from schema import *

# Helper methods

DEFAULT_NUM = 10
MAX_NUM = 50


def get_items(session, item_type: Optional[ItemType] = None,
              by: Optional[str] = None, before_time: Optional[int] = None, after_time: Optional[int] = None,
              min_score: Optional[int] = None, max_score: Optional[int] = None,
              min_comments: Optional[int] = None, max_comments: Optional[int] = None,
              sort_by: Union[SortBy, None] = None, sort_order: Union[SortOrder, None] = None,
              skip: int = 0, limit: int = 10, query: Optional[str] = None):
    if limit > MAX_NUM:
        limit = MAX_NUM

    if item_type is not None:
        item_type = item_type.value
    items_query = session.query(Item).filter(Item.type == item_type)
    items_query = items_query.options(noload(Item.kids))

    # Filtering
    if by is not None:
        items_query = items_query.filter(Item.by == by)
    if before_time is not None:
        items_query = items_query.filter(Item.time <= before_time)
    if after_time is not None:
        items_query = items_query.filter(Item.time >= after_time)
    if min_score is not None:
        items_query = items_query.filter(Item.score >= min_score)
    if max_score is not None:
        items_query = items_query.filter(Item.score <= max_score)
    if min_comments is not None:
        items_query = items_query.filter(Item.descendants >= min_comments)
    if max_comments is not None:
        items_query = items_query.filter(Item.descendants <= max_comments)
    if query is not None:
        items_query = items_query.filter(
            or_(Item.title.contains(query), Item.text.contains(query)))

    # Sorting
    if sort_by is not None:
        sort_column = getattr(Item, sort_by.value)
        if sort_order == SortOrder.asc:
            items_query = items_query.order_by(sort_column.asc())
        elif sort_order == SortOrder.desc:
            items_query = items_query.order_by(sort_column.desc())

    # Limit & skip
    items_query = items_query.offset(skip).limit(limit)
    # print(items_query)
    return items_query.all()


async def semantic_search(search_index, query, limit, exclude_comments):
    query = query.strip()
    cursor = dbsync.conn.cursor()

    # Perform semantic search
    start = time.time()
    results = await search_index.search(query)
    search_time = time.time() - start

    # Rank results
    start = time.time()
    results = compute_rankings(cursor, query, results)
    rank_time = time.time() - start
    print(
        f"search({search_time:.3f}) rank({rank_time:.3f}) num({len(results)} -> {limit}): '{query}'")
    results = results[:limit]

    # Fetch stories and their comments
    stories = []
    for (_, story_id) in results:
        cursor.execute(f"SELECT * FROM items WHERE id = {story_id}")
        story_row = cursor.fetchone()
        if story_row:
            story = Item(**dict(story_row))
            if not exclude_comments:
                story.comment_text = get_comments_text(cursor, story_id)
            stories.append(story)
    cursor.close()
    return stories


def normalize(values, reverse=False):
    min_val = min(values)
    max_val = max(values)
    normalized_values = [(value - min_val) / (max_val - min_val)
                         for value in values]
    if reverse:
        normalized_values = [1 - value for value in normalized_values]
    return normalized_values


def compute_rankings(cursor, query, results):
    expanded = []
    for (story_id, distance) in results:
        cursor.execute(
            "SELECT title, score, time FROM items WHERE id = ?", (story_id,))
        title, score, age = cursor.fetchone()
        if title is None:
            continue
        score = 1 if score is None else score
        age = 0 if age is None else age
        expanded.append((story_id, distance, title, score, age))

    scores, ages, distances = zip(
        *[(score, age, distance) for _, distance, _, score, age in expanded])
    normalized_scores = normalize(scores)
    normalized_ages = normalize(ages)
    normalized_distances = normalize(distances, reverse=True)

    w1, w2, w3, w4 = 0.4, 0.4, 0.1, 0.1

    rankings = []
    for i, (story_id, distance, title, _, _) in enumerate(expanded):
        query_words = set(word.lower() for word in query.split())
        title_words = set(word.lower() for word in title.split())
        matches = len(query_words.intersection(title_words))
        direct_match_boost = w4 * matches

        score_rank = w1 * normalized_scores[i] \
            + w2 * normalized_distances[i] + \
            w3 * normalized_ages[i] + direct_match_boost
        rankings.append((score_rank, story_id))

    return sorted(rankings, reverse=True)


# Top 10 kid comments, and first child comment of each from the database
def get_comments_text(cursor, story_id):
    comment_text = []
    cursor.execute(f"""SELECT i.* FROM items i
                    JOIN kids k ON i.id = k.kid
                    WHERE k.item = {story_id} AND i.type = 'comment'
                    ORDER BY k.display_order
                    LIMIT 10""")
    comments = [Item(**dict(row)) for row in cursor.fetchall()]
    for comment in comments:
        if comment.text:
            comment_text.append(comment.text)
            cursor.execute(f"""SELECT i.* FROM items i
                            JOIN kids k ON i.id = k.kid
                            WHERE k.item = {comment.id} AND i.type = 'comment'
                            ORDER BY k.display_order
                            LIMIT 1""")
            child_row = cursor.fetchone()
            if child_row:
                child_comment = Item(**dict(child_row))
                if child_comment.text:
                    comment_text.append(child_comment.text)
    return comment_text
