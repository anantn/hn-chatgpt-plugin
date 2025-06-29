import os
import requests
import datetime

from dateutil.relativedelta import relativedelta
from requests.exceptions import HTTPError
from fastapi import Depends, Query, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.responses import FileResponse
from gunicorn.app.base import BaseApplication
from prometheus_fastapi_instrumentator import Instrumentator

from sqlalchemy import or_, select, create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, load_only
from typing import List, Optional


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

# Metrics password. If not provided, metrics are not exposed.
PASSWD = os.environ.get("PASSWD")
security = HTTPBasic()


def check_basic_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.password == PASSWD:
        return True
    else:
        raise HTTPException(status_code=401, detail="Unauthorized")


# SQLachemy setup
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
instrumentator = Instrumentator().instrument(app)

# API endpoints


@app.on_event("startup")
async def _startup():
    if PASSWD is not None:
        instrumentator.expose(
            app, include_in_schema=False, dependencies=[Depends(check_basic_auth)]
        )


@app.get("/.well-known/ai-plugin.json", include_in_schema=False)
def get_plugin():
    return FileResponse("static/ai-plugin.json")


@app.get("/openapi.yaml", include_in_schema=False)
def get_openapi_spec():
    return FileResponse("static/openapi.yaml")


@app.get("/legal.html", include_in_schema=False)
def get_legal():
    return FileResponse("static/legal.html")


@app.get("/hn.jpg", include_in_schema=False)
def get_image():
    return FileResponse("static/hn.jpg")


@app.get("/", include_in_schema=False)
def get_root():
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
    with_answer: Optional[bool] = False,
):
    if limit < 3:
        limit = 3
    if limit > utils.MAX_NUM:
        limit = utils.MAX_NUM
    if before_time:
        before_time = utils.parse_human_time(before_time)
    if after_time:
        after_time = utils.parse_human_time(after_time)

    # If upper bound was specified, but no lower bound, let's add one to one year earlier
    if before_time and not after_time:
        lower_bound = datetime.datetime.fromtimestamp(before_time) - relativedelta(
            years=1
        )
        after_time = lower_bound.timestamp()

    session = scoped_session()

    if query is not None:
        query = " ".join(query.lower().split())

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
                with_answer,
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
    if not exclude_text:
        results = utils.with_top_comments(session, results)

    # Add answer if needed
    if with_answer:
        results = utils.with_answer(session, query, results)

    return jsonable_encoder(results)


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


class UvicornGunicornApplication(BaseApplication):
    def __init__(self, app, options=None):
        self.options = options or {}
        self.application = app
        super().__init__()

    def load_config(self):
        config = {
            key: value
            for key, value in self.options.items()
            if key in self.cfg.settings and value is not None
        }
        for key, value in config.items():
            self.cfg.set(key.lower(), value)

    def load(self):
        return self.application


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

    # Front uvicorn with gunicorn
    options = {
        "bind": f"0.0.0.0:{PORT}",
        "workers": 8,
        "worker_class": "uvicorn.workers.UvicornWorker",
    }
    gunicorn_app = UvicornGunicornApplication(app, options)
    gunicorn_app.run()
