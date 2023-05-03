import enum
import json
import datetime

from typing import Optional, List
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Table
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from pydantic import BaseModel, Field, validator
from fastapi.openapi.utils import get_openapi

# Define SQLAlchemy models
Base = declarative_base()


class ItemType(enum.Enum):
    story = 'story'
    comment = 'comment'
    poll = 'poll'
    job = 'job'


class SortBy(enum.Enum):
    relevance = 'relevance'
    score = 'score'
    time = 'time'
    descendants = 'descendants'


class UserSortBy(enum.Enum):
    created = 'created'
    karma = 'karma'


class SortOrder(enum.Enum):
    asc = 'asc'
    desc = 'desc'


class Verbosity(enum.Enum):
    full = 'full'
    none = 'none'
    short = 'short'


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    deleted = Column(Boolean)
    type = Column(String)
    by = Column(String)
    time = Column(Integer)
    text = Column(String)
    dead = Column(Boolean)
    parent = Column(Integer)
    poll = Column(Integer)
    url = Column(String)
    score = Column(Integer)
    title = Column(String)
    parts = Column(String)
    descendants = Column(Integer)


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    created = Column(Integer)
    karma = Column(Integer)
    about = Column(String)
    submitted = Column(String)


story_comments = Table(
    "kids",
    Base.metadata,
    Column("item", Integer, ForeignKey("items.id")),
    Column("kid", Integer, ForeignKey("items.id")),
    Column("display_order", Integer)
)


class FullItem(Item):
    kids = relationship("FullItem", secondary=story_comments,
                        primaryjoin=Item.id == story_comments.c.item,
                        secondaryjoin=Item.id == story_comments.c.kid,
                        order_by=story_comments.c.display_order)

# Define Pydantic models for API responses


class ItemResponse(BaseModel):
    id: int
    type: str
    by: Optional[str] = None
    time: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    score: Optional[int] = 0
    title: Optional[str] = None
    descendants: Optional[int] = 0

    parent: Optional[int] = None
    summary: Optional[List[str]] = []

    parts: Optional[List[dict]] = None
    hn_url: Optional[str] = Field(None)

    @validator("hn_url", pre=True, always=True)
    def set_hn_url(cls, v, values):
        id = values.get("id")
        if id:
            return f"https://news.ycombinator.com/item?id={id}"
        return v

    @validator("time", pre=True)
    def set_time(cls, value: Optional[int]) -> Optional[str]:
        if value is not None:
            return datetime.datetime.fromtimestamp(value).strftime("%b %d, %Y %H:%M")
        return value


class FullItemResponse(ItemResponse):
    kids: Optional[List['FullItemResponse']] = []

    class Config:
        orm_mode = True


class UserResponse(BaseModel):
    id: str
    created: str
    karma: int
    about: Optional[str] = None
    submitted: Optional[List[int]] = None
    hn_url: Optional[str] = Field(None)

    class Config:
        orm_mode = True

    @validator("hn_url", pre=True, always=True)
    def set_hn_url(cls, v, values):
        id = values.get("id")
        if id:
            return f"https://news.ycombinator.com/user?id={id}"
        return v

    @validator("created", pre=True)
    def set_created(cls, value: int) -> str:
        if value is not None:
            return datetime.datetime.fromtimestamp(value).strftime("%b %d, %Y %H:%M")
        return value

    @validator("submitted", pre=True)
    def parse_submitted(cls, value: Optional[str]) -> Optional[List[int]]:
        if value is not None:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return []
        return None

# OpenAPI schema customization


def get_schema(app):
    openapi_schema = get_openapi(
        title="Hacker News API for ChatGPT",
        version="0.1",
        routes=app.routes,
    )
    openapi_schema["info"] = {
        "title": "Hacker News API for ChatGPT",
        "version": "0.1",
        "description": "Query, analyze, and summarize insights from the Hacker News community.",
        "contact": {
            "url": "https://hn.kix.in/",
            "email": "anant@kix.in"
        },
        "license": {
            "name": "MIT License",
            "url": "https://opensource.org/license/mit/"
        },
    }

    openapi_schema["paths"]["/item"]["get"]["summary"] = \
        "Retrieve a story, poll, or comment; along with all of their children."
    openapi_schema["paths"]["/item"]["get"]["parameters"][0]["description"] = \
        "ID of the item you want to retrieve."
    openapi_schema["paths"]["/item"]["get"]["parameters"][1]["description"] = \
        "Set this to control the length of the output. Value of 'full' will retrieve all kid comments (default), 'short' will return the most relevant kid comments, and 'none' will return only the item metadata."

    openapi_schema["paths"]["/items"]["get"]["summary"] = \
        "Search for items matching a variety of criteria. Items are sorted by their relevance to the query by default."
    openapi_schema["paths"]["/items"]["get"]["parameters"][0]["description"] = \
        "Restrict results to this type. Can be 'story' (default), 'comment', 'poll', or 'job'."
    openapi_schema["paths"]["/items"]["get"]["parameters"][1]["description"] = \
        "Perform a semantic search to find all items matching the meaning of this query string."
    openapi_schema["paths"]["/items"]["get"]["parameters"][2]["description"] = \
        "Exclude text and selected child comments if set to true, default is false."
    openapi_schema["paths"]["/items"]["get"]["parameters"][3]["description"] = \
        "Find items created or submitted by this user."
    openapi_schema["paths"]["/items"]["get"]["parameters"][4]["description"] = \
        "Find items submitted at or before this time. You may specify the time in natural language."
    openapi_schema["paths"]["/items"]["get"]["parameters"][5]["description"] = \
        "Find items submitted at or after this time. You may specify the time in natural language."
    openapi_schema["paths"]["/items"]["get"]["parameters"][6]["description"] = \
        "Find items with a score equal or higher than this number."
    openapi_schema["paths"]["/items"]["get"]["parameters"][7]["description"] = \
        "Find items with a score equal or lower than this number."
    openapi_schema["paths"]["/items"]["get"]["parameters"][8]["description"] = \
        "Find items with a number of comments (descendants) equal or higher than this number."
    openapi_schema["paths"]["/items"]["get"]["parameters"][9]["description"] = \
        "Find items with a number of comments (descendants) or lower than this number."
    openapi_schema["paths"]["/items"]["get"]["parameters"][10]["description"] = \
        "Sort results by relevance (default), score (upvotes), descendants (number of comments), time (of submission)."
    openapi_schema["paths"]["/items"]["get"]["parameters"][11]["description"] = \
        "Sort results in descending (default) or ascending order of the sort_by parameter."
    openapi_schema["paths"]["/items"]["get"]["parameters"][12]["description"] = \
        "Offset the results returned, use to page through multiple results."
    openapi_schema["paths"]["/items"]["get"]["parameters"][13]["description"] = \
        "Limit the number of results returned (default 10, max 50)."

    openapi_schema["paths"]["/user"]["get"]["summary"] = \
        "Retrieve a user along with all their submissions (story, comment, or poll IDs)."
    openapi_schema["paths"]["/user"]["get"]["parameters"][0]["description"] = \
        "ID of the user you want to retrieve."

    openapi_schema["paths"]["/users"]["get"]["summary"] = \
        "Find users matching a variety of criteria. Users are sorted by their karma (upvotes) by default."
    openapi_schema["paths"]["/users"]["get"]["parameters"][0]["description"] = \
        "Find users created at or before this time. You may specify the time in natural language."
    openapi_schema["paths"]["/users"]["get"]["parameters"][1]["description"] = \
        "Find users created at or after this time. You may specify the time in natural language."
    openapi_schema["paths"]["/users"]["get"]["parameters"][2]["description"] = \
        "Find users with karma at or above this number."
    openapi_schema["paths"]["/users"]["get"]["parameters"][3]["description"] = \
        "Find users with karma at or below this number."
    openapi_schema["paths"]["/users"]["get"]["parameters"][4]["description"] = \
        "Sort results by karma (default), or created (account creation time)."
    openapi_schema["paths"]["/users"]["get"]["parameters"][5]["description"] = \
        "Sort results in descending (default) or ascending order of the sort_by parameter."
    openapi_schema["paths"]["/users"]["get"]["parameters"][6]["description"] = \
        "Offset the results returned, use to page through multiple results."
    openapi_schema["paths"]["/users"]["get"]["parameters"][7]["description"] = \
        "Limit the number of results returned (default 10, max 50)."

    openapi_schema["components"]["schemas"]["SortBy"]["description"] = \
        "Use to ensure item results are sorted by a specific value. Defaults to 'relevance'. Valid values are 'score', 'descendants', or 'time'."
    openapi_schema["components"]["schemas"]["SortOrder"]["description"] = \
        "Use to ensure item results are sorted in a specific order. Defaults to 'desc'. Valid values are 'desc' or 'asc'."
    openapi_schema["components"]["schemas"]["UserSortBy"]["description"] = \
        "Use to ensure user results are sorted by a specific value. Defaults to 'karma'. Valid values are 'karma' or 'created'."

    return openapi_schema
