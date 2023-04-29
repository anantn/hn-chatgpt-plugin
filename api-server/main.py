import os
import copy
import dbsync
import uvicorn
import asyncio
import embedder

from fastapi import Query, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse

from sqlalchemy import select, create_engine, or_
from sqlalchemy.orm import sessionmaker, scoped_session, noload
from typing import List, Optional, Union
from schema import *

# Database connection
DB_PATH = os.path.expanduser(os.environ.get("DB_PATH"))
if not DB_PATH:
    print("Please set the DB_PATH environment variable to the path of the SQLite database.")
    exit()

engine = create_engine(
    f"sqlite:///{DB_PATH}?mode=ro", connect_args={"check_same_thread": False})
session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
scoped_session = scoped_session(session_factory)
Base.metadata.create_all(bind=engine)

app = FastAPI()


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

# Helper CRUD method


def get_items(item_type: Optional[ItemType] = None, ids: Optional[List[int]] = None,
              by: Optional[str] = None, before_time: Optional[int] = None, after_time: Optional[int] = None,
              min_score: Optional[int] = None, max_score: Optional[int] = None,
              min_comments: Optional[int] = None, max_comments: Optional[int] = None,
              sort_by: Union[SortBy, None] = None, sort_order: Union[SortOrder, None] = None,
              query: Optional[str] = None, skip: int = 0, limit: int = 10):
    if limit > 100:
        limit = 100

    session = scoped_session()
    if item_type is not None:
        item_type = item_type.value
    items_query = session.query(Item).filter(Item.type == item_type)
    items_query = items_query.options(noload(Item.kids))

    # Filtering
    if ids is not None:
        items_query = items_query.filter(Item.id.in_(ids))
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

# API endpoints


@app.get("/.well-known/ai-plugin.json")
def get_plugin():
    return FileResponse("static/ai-plugin.json")


@app.get("/openapi.yaml")
def get_plugin():
    return FileResponse("static/openapi.yaml")


@app.get("/hn.jpg")
def get_plugin():
    return FileResponse("static/hn.jpg")


@app.get("/story", response_model=StoryResponse)
def get_story(id: int = Query(1)):
    session = scoped_session()
    story = session.query(Item).filter(
        Item.type == ItemType.story.value).filter(Item.id == id).first()
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return story


@app.get("/stories", response_model=List[StoryResponse])
def get_stories(ids: Optional[List[int]] = Query(None), by: Optional[str] = None,
                before_time: Optional[int] = None, after_time: Optional[int] = None,
                min_score: Optional[int] = None, max_score: Optional[int] = None,
                min_comments: Optional[int] = None, max_comments: Optional[int] = None,
                sort_by: Union[SortBy, None] = None, sort_order: Union[SortOrder, None] = None,
                query: Optional[str] = None, skip: int = 0, limit: int = 10):
    if sort_by is None and sort_order is None:
        sort_by = SortBy.score
        sort_order = SortOrder.desc
    return get_items(item_type=ItemType.story, ids=ids, by=by, before_time=before_time, after_time=after_time,
                     min_score=min_score, max_score=max_score, min_comments=min_comments, max_comments=max_comments,
                     sort_by=sort_by, sort_order=sort_order, query=query, skip=skip, limit=limit)


@app.get("/comment", response_model=CommentResponse)
def get_comment(id: int = Query(1)):
    session = scoped_session()
    comment = session.query(Item).filter(
        Item.type == ItemType.comment.value).filter(Item.id == id).first()
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment


@app.get("/comments", response_model=List[CommentResponse])
def get_comments(ids: Optional[List[int]] = Query(None), by: Optional[str] = None,
                 before_time: Optional[int] = None, after_time: Optional[int] = None,
                 sort_by: Union[SortBy, None] = None, sort_order: Union[SortOrder, None] = None,
                 query: Optional[str] = None, skip: int = 0, limit: int = 50):
    if sort_by is None and sort_order is None:
        sort_by = SortBy.time
        sort_order = SortOrder.desc
    return get_items(item_type=ItemType.comment, ids=ids, by=by, before_time=before_time, after_time=after_time,
                     sort_by=sort_by, sort_order=sort_order, query=query, skip=skip, limit=limit)


@app.get("/polls", response_model=List[PollResponse])
def get_polls(ids: Optional[List[int]] = Query(None), by: Optional[str] = None,
              before_time: Optional[int] = None, after_time: Optional[int] = None,
              sort_by: Union[SortBy, None] = None, sort_order: Union[SortOrder, None] = None,
              query: Optional[str] = None, skip: int = 0, limit: int = 10):
    if sort_by is None and sort_order is None:
        sort_by = SortBy.score
        sort_order = SortOrder.desc
    items = get_items(item_type=ItemType.poll, ids=ids, by=by, before_time=before_time, after_time=after_time,
                      sort_by=sort_by, sort_order=sort_order, query=query, skip=skip, limit=limit)
    if len(items) == 0:
        return []

    session = scoped_session()
    poll_responses = []
    for item in items:
        working_item = copy.copy(item)
        item_parts = [int(part_id)
                      for part_id in working_item.parts.split(",")]
        working_item.parts = None
        item_pollopts = session.query(Item).filter(
            Item.id.in_(item_parts)).all()
        parts = [ItemResponse.from_orm(pollopt) for pollopt in item_pollopts]
        poll_response = PollResponse.from_orm(working_item)
        poll_response.parts = parts
        poll_responses.append(poll_response)
    return poll_responses


@app.get("/user", response_model=UserResponse)
def get_user(id: str = Query("pg")):
    session = scoped_session()
    user = session.query(User).filter(User.id == id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/users", response_model=List[UserResponse])
def get_users(ids: Optional[List[str]] = Query(None),
              before_created: Optional[int] = None, after_created: Optional[int] = None,
              min_karma: Optional[int] = None, max_karma: Optional[int] = None,
              sort_by: Union[UserSortBy, None] = None, sort_order: Union[SortOrder, None] = None,
              skip: int = 0, limit: int = 10):
    if limit > 100:
        limit = 100
    session = scoped_session()

    # Select columns except submitted
    columns = [User.id, User.created, User.karma, User.about]
    user_select = select(*columns)

    # Filtering
    if ids is not None:
        user_select = user_select.where(User.id.in_(ids))
    if before_created is not None:
        user_select = user_select.where(User.created <= before_created)
    if after_created is not None:
        user_select = user_select.where(User.created >= after_created)
    if min_karma is not None:
        user_select = user_select.where(User.karma >= min_karma)
    if max_karma is not None:
        user_select = user_select.where(User.karma <= max_karma)

    # Sorting
    if sort_by is None:
        user_select = user_select.order_by(User.karma.desc())
    else:
        sort_column = getattr(User, sort_by.value)
        if sort_order == SortOrder.asc:
            user_select = user_select.order_by(sort_column.asc())
        elif sort_order == SortOrder.desc:
            user_select = user_select.order_by(sort_column.desc())

    user_select = user_select.offset(skip).limit(limit)
    return session.execute(user_select).fetchall()


async def main():
    # Initialize embedder model for reuse
    encoder = embedder.Embedder()
    encoder_task = asyncio.create_task(encoder._process_requests())
    doc_encoder = embedder.DocumentEmbedder(DB_PATH, encoder)

    # Catch up on all data updates
    # Disable embedder task for now
    # updates, embedder_task = await dbsync.run(DB_PATH, doc_encoder)
    updates = await dbsync.run(DB_PATH)

    # Catch up on document embeddings, offset = go back 100 stories and refresh
    await doc_encoder.process_catchup_stories(100)

    # Start API server
    server = uvicorn.Server(uvicorn.Config(
        app, port=8000, log_level="info", reload=True))
    uvicorn_task = asyncio.create_task(server.serve())

    # If any task aborts, cancel the others and abort program
    _, pending = await asyncio.wait(
        {updates, encoder_task, uvicorn_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()

    dbsync.shutdown()
    print("Exiting...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...")
        dbsync.shutdown()
