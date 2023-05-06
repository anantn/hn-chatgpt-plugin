import os
import time
import openai
import sqlite3
import logging
import tiktoken
import dateparser

from sqlalchemy.sql import text
from fastapi.middleware.cors import CORSMiddleware
from asgi_logger import AccessLoggerMiddleware

from schema import *

# Helper methods

DEFAULT_NUM = 10
MAX_NUM = 25
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

# OpenAI constants
ENCODER_NAME = "cl100k_base"
TOKEN_LIMIT = 3840  # 4096-256, leave 256 for answer and user query
OAI_CACHE = {}


def num_tokens(string: str) -> int:
    encoding = tiktoken.get_encoding(ENCODER_NAME)
    num_tokens = len(encoding.encode(string))
    return num_tokens


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

    logging.getLogger("uvicorn.access").handlers = []
    app.add_middleware(
        AccessLoggerMiddleware, format='%(h)s - %(s)s %(M)sms - "%(request_line)s"'
    )
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

    tiktoken.get_encoding(ENCODER_NAME)
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


def with_answer(session, query, items):
    if os.environ.get("OPENAI_API_KEY") is None:
        return items
    if query in OAI_CACHE:
        items[0].answer = OAI_CACHE[query]
        return items

    system = (
        "You are a helpful assistant that can answer questions accurately and concisely, "
        "based on text from on forum discussions on Hacker News."
    )
    prompt = f"Given the following hacker news discussions:\n\n"
    for item in items:
        if item.title:
            prompt += f"{item.title}\n"
        if item.text:
            prompt += f"{item.text}\n"
    prompt += "\n"

    # Keep adding comments until we run out of tokens.
    remaining_tokens = TOKEN_LIMIT - num_tokens(system + prompt)
    for item in items:
        if remaining_tokens <= 0:
            break

        comments = get_comments_text(session, item.id, x_top=5, n_child=0)
        for comment in comments:
            comment_token_count = num_tokens(comment)
            if remaining_tokens >= comment_token_count:
                prompt += f"{comment}\n"
                remaining_tokens -= comment_token_count
            else:
                # Truncate the last comment to fit within the token limit
                encoding = tiktoken.get_encoding(ENCODER_NAME)
                truncated_comment = encoding.decode(
                    encoding.encode(comment)[:remaining_tokens]
                )
                prompt += f"{truncated_comment}\n"
                remaining_tokens = 0
                break

    prompt += f"\n\nAnswer the following question: {query}?"

    start = time.time()
    resp = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": system,
            },
            {"role": "user", "content": prompt},
        ],
    )
    end = time.time() - start

    if resp and "choices" in resp and len(resp["choices"]) > 0:
        if resp["choices"][0]["message"] and "content" in resp["choices"][0]["message"]:
            items[0].answer = resp["choices"][0]["message"]["content"]
            print(f"openai answer({end:.2f}s): '{items[0].answer}'")
            OAI_CACHE[query] = items[0].answer

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
