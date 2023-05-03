import copy

from sqlalchemy.sql import text
from fastapi.middleware.cors import CORSMiddleware

from schema import *

# Helper methods

DEFAULT_NUM = 10
MAX_NUM = 50


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
    polls = []
    for item in items:
        poll = copy.copy(item)
        if item.parts is not None:
            poll_parts = [int(part_id)
                          for part_id in item.parts.split(",")]
            pollopts = session.query(Item.id, Item.type, Item.text, Item.score).filter(
                Item.id.in_(poll_parts)).all()
            poll.parts = []
            for pollopt in pollopts:
                if pollopt.text and pollopt.score:
                    poll.parts.append(
                        {"text": pollopt.text, "score": pollopt.score})
        polls.append(poll)
    return polls
