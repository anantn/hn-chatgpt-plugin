import copy
import dateparser

from sqlalchemy.sql import text
from fastapi.middleware.cors import CORSMiddleware

from schema import *

# Helper methods

DEFAULT_NUM = 10
MAX_NUM = 25
EXAMPLE_QUESTIONS = [
    "best laptop for coding that isn't from apple",
    "who was acquired by mozilla",
    "how can i land a job at faang?",
    "help me find true love",
    "what's it like working at an early stage startup",
    "top data science tools i should learn",
    "interesting articles about astronomy",
    "latest breakthroughs in battery technology",
    "how do i become a great manager?",
    "effective strategies for overcoming procrastination",
]


def example_questions(as_json=False):
    if as_json:
        return json.dumps(EXAMPLE_QUESTIONS, ensure_ascii=False).encode("utf8")
    return EXAMPLE_QUESTIONS


def initialize_middleware(app):
    def set_schema():
        if app.openapi_schema:
            return app.openapi_schema
        app.openapi_schema = get_schema(app)
        return app.openapi_schema

    app.openapi = set_schema
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


def parse_human_time(time_str):
    if time_str:
        time_str = " ".join(time_str.lower().split())
        time_str = time_str.replace("couple", "2")
        time_str = time_str.replace("a couple", "2")
        time_str = time_str.replace("few", "3")
        time_str = time_str.replace("a few", "3")
        time_str = time_str.replace("several", "3")
        time_str = time_str.replace("rignt now", "now")
        time_str = time_str.replace("around now", "now")
        time_str = dateparser.parse(time_str)
        if time_str:
            return time_str.timestamp()
    return None


def with_top_comments(session, items):
    x_top = 2
    n_child = 1
    if len(items) > 3:
        x_top = 1
        n_child = 1
    if len(items) > 5:
        x_top = 1
        n_child = 0
    if len(items) > 7:
        x_top = 0
        n_child = 0

    for item in items:
        item.top_comments = get_comments_text(session, item.id, x_top, n_child)
    return items


# Top 'x' kid comments, and 'n' child comment of each top-level comment from the database
# TODO: limit to word count instead of comment count and find smarter way to rank
def get_comments_text(session, story_id, x_top=3, n_child=1):
    comment_text = []
    cursor = session.execute(
        text(
            f"""SELECT i.* FROM items i
                    JOIN kids k ON i.id = k.kid
                    WHERE k.item = {story_id} AND i.type = 'comment'
                    ORDER BY k.display_order
                    LIMIT {x_top}"""
        )
    ).cursor
    column_names = [desc[0] for desc in cursor.description]
    comments = [Item(**dict(zip(column_names, row))) for row in cursor.fetchall()]
    for comment in comments:
        if comment.text:
            comment_text.append(comment.text)
            if n_child > 0:
                cursor = session.execute(
                    text(
                        f"""SELECT i.* FROM items i
                                JOIN kids k ON i.id = k.kid
                                WHERE k.item = {comment.id} AND i.type = 'comment'
                                ORDER BY k.display_order
                                LIMIT {n_child}"""
                    )
                ).cursor
                child_row = cursor.fetchone()
                if child_row:
                    child_comment = Item(**dict(zip(column_names, child_row)))
                    if child_comment.text:
                        comment_text.append(child_comment.text)
    return comment_text


# Populate parts with the poll responses
def get_poll_responses(session, items):
    polls = []
    for item in items:
        poll = copy.copy(item)
        if item.parts is not None:
            poll_parts = [int(part_id) for part_id in item.parts.split(",")]
            pollopts = (
                session.query(Item.id, Item.type, Item.text, Item.score)
                .filter(Item.id.in_(poll_parts))
                .all()
            )
            poll.parts = []
            for pollopt in pollopts:
                if pollopt.text and pollopt.score:
                    poll.parts.append({"text": pollopt.text, "score": pollopt.score})
        polls.append(poll)
    return polls
