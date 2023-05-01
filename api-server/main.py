import os
import copy
import uvicorn
import requests

from requests.exceptions import HTTPError
from fastapi import Query, FastAPI, HTTPException
from starlette.responses import FileResponse

from sqlalchemy import select, create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from typing import List, Optional

import utils
from schema import *

# Database connection
PORT = 8000
DB_PATH = os.path.expanduser(os.environ.get("DB_PATH"))
if not DB_PATH:
    print("Please set the DB_PATH environment variable to the path of the SQLite database.")
    exit()
DATA_SERVER = f"http://localhost:{PORT+1}/search"

engine = create_engine(
    f"sqlite:///{DB_PATH}?mode=ro", connect_args={"check_same_thread": False}, echo=False)
session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
scoped_session = scoped_session(session_factory)
Base.metadata.create_all(bind=engine)
app = FastAPI()
app = utils.initialize_middleware(app)


# API endpoints


@app.get("/.well-known/ai-plugin.json", include_in_schema=False)
def get_plugin():
    return FileResponse("static/ai-plugin.json")


@app.get("/openapi.yaml", include_in_schema=False)
def get_openapi_spec():
    return FileResponse("static/openapi.yaml")


@app.get("/hn.jpg", include_in_schema=False)
def get_image():
    return FileResponse("static/hn.jpg")


@app.get("/", include_in_schema=False)
def get_image():
    return FileResponse("static/index.html")


@app.get("/search", response_model=List[StoryResponse])
def search_stories(query: str, limit: int = 1, exclude_comments: bool = False):
    if limit > 5:
        if exclude_comments:
            if limit > 20:
                limit = 20
        else:
            limit = 3
    session = scoped_session()
    return utils.semantic_search(DATA_SERVER, session, query, limit, exclude_comments)


@app.get("/story", response_model=StoryResponse)
def get_story(id: int = Query(1)):
    session = scoped_session()
    story = session.query(Item).filter(
        Item.type == ItemType.story.value).filter(Item.id == id).first()
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return story


@app.get("/stories", response_model=List[StoryResponse])
def get_stories(by: Optional[str] = Query(None),
                before_time: Optional[int] = None, after_time: Optional[int] = None,
                min_score: Optional[int] = None, max_score: Optional[int] = None,
                min_comments: Optional[int] = None, max_comments: Optional[int] = None,
                sort_by: SortBy = SortBy.score, sort_order: SortOrder = SortOrder.desc,
                skip: int = 0, limit: int = utils.DEFAULT_NUM):
    session = scoped_session()
    return utils.get_items(session, item_type=ItemType.story, by=by, before_time=before_time, after_time=after_time,
                           min_score=min_score, max_score=max_score, min_comments=min_comments, max_comments=max_comments,
                           sort_by=sort_by, sort_order=sort_order, skip=skip, limit=limit)


@app.get("/comment", response_model=CommentResponse)
def get_comment(id: int = Query(1)):
    session = scoped_session()
    comment = session.query(Item).filter(
        Item.type == ItemType.comment.value).filter(Item.id == id).first()
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment


@app.get("/comments", response_model=List[CommentResponse])
def get_comments(by: Optional[str] = Query(None),
                 before_time: Optional[int] = None, after_time: Optional[int] = None,
                 sort_by: SortBy = SortBy.time, sort_order: SortOrder = SortOrder.desc,
                 skip: int = 0, limit: int = utils.DEFAULT_NUM):
    session = scoped_session()
    return utils.get_items(session, item_type=ItemType.comment, by=by, before_time=before_time, after_time=after_time,
                           sort_by=sort_by, sort_order=sort_order, skip=skip, limit=limit)


@app.get("/polls", response_model=List[PollResponse])
def get_polls(by: Optional[str] = Query(None),
              before_time: Optional[int] = None, after_time: Optional[int] = None,
              sort_by: SortBy = SortBy.score, sort_order: SortOrder = SortOrder.desc,
              skip: int = 0, limit: int = utils.DEFAULT_NUM, query: Optional[str] = None):
    session = scoped_session()
    items = utils.get_items(session, item_type=ItemType.poll, by=by, before_time=before_time, after_time=after_time,
                            sort_by=sort_by, sort_order=sort_order, skip=skip, limit=limit, query=query)
    if len(items) == 0:
        return []

    session = scoped_session()
    poll_responses = []
    for item in items:
        working_item = copy.copy(item)
        if working_item.parts is not None:
            item_parts = [int(part_id)
                          for part_id in working_item.parts.split(",")]
            working_item.parts = None
            item_pollopts = session.query(Item).filter(
                Item.id.in_(item_parts)).all()
            parts = [ItemResponse.from_orm(pollopt)
                     for pollopt in item_pollopts]
        else:
            parts = []
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
def get_users(before_created: Optional[int] = None, after_created: Optional[int] = None,
              min_karma: Optional[int] = None, max_karma: Optional[int] = None,
              sort_by: UserSortBy = UserSortBy.karma, sort_order: SortOrder = SortOrder.desc,
              skip: int = 0, limit: int = utils.DEFAULT_NUM):
    if limit > utils.MAX_NUM:
        limit = utils.MAX_NUM
    session = scoped_session()

    # Select columns except submitted
    columns = [User.id, User.created, User.karma, User.about]
    user_select = select(*columns)

    # Filtering
    if before_created is not None:
        user_select = user_select.where(User.created <= before_created)
    if after_created is not None:
        user_select = user_select.where(User.created >= after_created)
    if min_karma is not None:
        user_select = user_select.where(User.karma >= min_karma)
    if max_karma is not None:
        user_select = user_select.where(User.karma <= max_karma)

    # Sorting
    sort_column = getattr(User, sort_by.value)
    if sort_order == SortOrder.asc:
        user_select = user_select.order_by(sort_column.asc())
    elif sort_order == SortOrder.desc:
        user_select = user_select.order_by(sort_column.desc())

    user_select = user_select.offset(skip).limit(limit)
    return session.execute(user_select).fetchall()


if __name__ == "__main__":
    try:
        params = {"query": "test"}
        req = requests.get(DATA_SERVER, params=params)
        _ = req.json()
    except (HTTPError, Exception):
        print(f"Please run the data server first!")
        exit(1)
    uvicorn.run(app, port=PORT)
