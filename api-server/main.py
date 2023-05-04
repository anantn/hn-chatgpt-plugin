import os
import uvicorn
import requests

from requests.exceptions import HTTPError
from fastapi import Query, FastAPI, HTTPException
from starlette.responses import FileResponse

from sqlalchemy import or_, select, create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, load_only
from typing import List, Optional
from fastapi.encoders import jsonable_encoder

import utils
from search import search
from schema import *

# Database connection
PORT = 8000
DB_PATH = os.path.expanduser(os.environ.get("DB_PATH"))
if not DB_PATH:
    print(
        "Please set the DB_PATH environment variable to the path of the SQLite database."
    )
    exit()
DATA_SERVER = f"http://localhost:{PORT+1}/search"

engine = create_engine(
    f"sqlite:///{DB_PATH}?mode=ro",
    connect_args={"check_same_thread": False},
    echo=False,
)
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


@app.get("/item", response_model=FullItemResponse, response_model_exclude_none=True)
def get_item(id: int = Query(1), verbosity: Verbosity = Verbosity.short):
    session = scoped_session()

    if verbosity == Verbosity.full:
        item_query = session.query(FullItem).filter(FullItem.id == id)
    else:
        item_query = session.query(Item).filter(Item.id == id)

    item = item_query.first()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    if verbosity == Verbosity.short:
        item.top_comments = utils.get_comments_text(session, item.id, x_top=5)

    # If item_type was poll, also add pollopts
    if item.type == ItemType.poll.value:
        items = utils.get_poll_responses(session, [item])
        item = items[0]

    return item


@app.get("/items", response_model=List[ItemResponse], response_model_exclude_none=True)
def get_items(
    item_type: ItemType = ItemType.story,
    query: Optional[str] = Query(None),
    exclude_text: Optional[bool] = False,
    by: Optional[str] = Query(None),
    before_time: Optional[str] = None,
    after_time: Optional[str] = None,
    min_score: Optional[int] = None,
    max_score: Optional[int] = None,
    min_comments: Optional[int] = None,
    max_comments: Optional[int] = None,
    sort_by: SortBy = SortBy.relevance,
    sort_order: SortOrder = SortOrder.desc,
    skip: int = 0,
    limit: int = utils.DEFAULT_NUM,
):
    if limit < 3:
        limit = 3
    if limit > utils.MAX_NUM:
        limit = utils.MAX_NUM
    if before_time:
        before_time = utils.parse_human_time(before_time)
    if after_time:
        after_time = utils.parse_human_time(after_time)

    session = scoped_session()

    # If query is not empty and type is story or comments, go the semantic search route
    if query is not None and item_type in [ItemType.story, ItemType.comment]:
        return jsonable_encoder(
            search(
                DATA_SERVER,
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
            )
        )

    # Set type and don't load any children by default
    items_query = session.query(Item)

    if exclude_text:
        fields = [
            Item.id,
            Item.type,
            Item.by,
            Item.time,
            Item.url,
            Item.score,
            Item.title,
            Item.descendants,
        ]
        items_query = items_query.options(load_only(*fields))
    if item_type is not None:
        items_query = items_query.filter(Item.type == item_type.value)

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

    # If query is set but type is 'poll' or 'job', just use contains
    if query is not None:
        items_query = items_query.filter(
            or_(Item.title.contains(query), Item.text.contains(query))
        )

    # Sorting
    if sort_by is not None:
        if sort_by == SortBy.relevance:
            sort_by = SortBy.score
        sort_column = getattr(Item, sort_by.value)
        if sort_order == SortOrder.asc:
            items_query = items_query.order_by(sort_column.asc())
        elif sort_order == SortOrder.desc:
            items_query = items_query.order_by(sort_column.desc())

    # Limit & skip
    items_query = items_query.offset(skip).limit(limit)
    results = items_query.all()

    # If item_type was poll, also add pollopts
    if item_type == ItemType.poll:
        results = utils.get_poll_responses(session, results)

    # Add top_comments if needed
    if exclude_text:
        return jsonable_encoder(results)
    return jsonable_encoder(utils.with_top_comments(session, results))


@app.get("/user", response_model=UserResponse)
def get_user(id: str = Query("pg")):
    session = scoped_session()
    user = session.query(User).filter(User.id == id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/users", response_model=List[UserResponse])
def get_users(
    before_created: Optional[str] = None,
    after_created: Optional[str] = None,
    min_karma: Optional[int] = None,
    max_karma: Optional[int] = None,
    sort_by: UserSortBy = UserSortBy.karma,
    sort_order: SortOrder = SortOrder.desc,
    skip: int = 0,
    limit: int = utils.DEFAULT_NUM,
):
    if limit < 3:
        limit = 3
    if limit > utils.MAX_NUM:
        limit = utils.MAX_NUM
    if before_created:
        before_created = utils.parse_human_time(before_created)
    if after_created:
        after_created = utils.parse_human_time(after_created)

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
        print("Testing data server...")
        for q in utils.example_questions():
            params = {"query": q}
            req = requests.get(DATA_SERVER, params=params)
            _ = req.json()
    except (HTTPError, Exception):
        print(f"Please run the data server first!")
        exit(1)
    uvicorn.run(app, port=PORT)
