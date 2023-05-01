import enum
import json
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Table
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from pydantic import BaseModel, Field, validator
from fastapi.openapi.utils import get_openapi

# Define SQLAlchemy models
Base = declarative_base()


class ItemType(enum.Enum):
    comment = 'comment'
    job = 'job'
    story = 'story'
    poll = 'poll'


class SortBy(enum.Enum):
    score = 'score'
    time = 'time'
    descendants = 'descendants'


class UserSortBy(enum.Enum):
    created = 'created'
    karma = 'karma'


class SortOrder(enum.Enum):
    asc = 'asc'
    desc = 'desc'


story_comments = Table(
    "kids",
    Base.metadata,
    Column("item", Integer, ForeignKey("items.id")),
    Column("kid", Integer, ForeignKey("items.id")),
    Column("display_order", Integer)
)


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
    kids = relationship("Item", secondary=story_comments,
                        primaryjoin=id == story_comments.c.item,
                        secondaryjoin=id == story_comments.c.kid,
                        order_by=story_comments.c.display_order)


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    created = Column(Integer)
    karma = Column(Integer)
    about = Column(String)
    submitted = Column(String)

# Define Pydantic models for API responses


class ItemResponse(BaseModel):
    id: int
    type: str
    text: Optional[str] = None
    time: Optional[int] = None
    by: Optional[str] = None
    hn_url: Optional[str] = Field(None)

    class Config:
        orm_mode = True

    @validator("hn_url", pre=True, always=True)
    def set_hn_url(cls, v, values):
        id = values.get("id")
        if id:
            return f"https://news.ycombinator.com/item?id={id}"
        return v


class CommentResponse(ItemResponse):
    parent: int
    kids: list['CommentResponse'] = []

    class Config:
        orm_mode = True


class StoryResponse(ItemResponse):
    title: Optional[str] = None
    url: Optional[str] = None
    score: Optional[int] = 0
    descendants: Optional[int] = 0
    kids: list['CommentResponse'] = []
    comment_text: Optional[List[str]] = None

    class Config:
        orm_mode = True


class PollResponse(ItemResponse):
    title: str
    score: Optional[int] = 0
    descendants: Optional[int] = 0
    parts: Optional[List[ItemResponse]] = None

    class Config:
        orm_mode = True


class UserResponse(BaseModel):
    id: str
    created: int
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

    openapi_schema["paths"]["/search"]["get"]["summary"] = \
        "Performs a semantic search on story title, text, and comments and returns matching stories with their comments."
    openapi_schema["paths"]["/search"]["get"]["parameters"][0]["description"] = \
        "Query string to search for."
    openapi_schema["paths"]["/search"]["get"]["parameters"][1]["description"] = \
        "Limit the number of results returned (default 1, max 3)."
    openapi_schema["paths"]["/search"]["get"]["parameters"][2]["description"] = \
        "Returns results without comment data. If this is set to true, max limit is increased to 20."

    openapi_schema["paths"]["/story"]["get"]["summary"] = \
        "Retrieve a story along with all its comments."
    openapi_schema["paths"]["/story"]["get"]["parameters"][0]["description"] = \
        "ID of the story you want to retrieve."

    openapi_schema["paths"]["/comment"]["get"]["summary"] = \
        "Retrieve a comment along with all its replies."
    openapi_schema["paths"]["/comment"]["get"]["parameters"][0]["description"] = \
        "ID of the comment you want to retrieve."

    openapi_schema["paths"]["/user"]["get"]["summary"] = \
        "Retrieve a user along with all their submissions (story, comment, or poll IDs)."
    openapi_schema["paths"]["/user"]["get"]["parameters"][0]["description"] = \
        "ID of the user you want to retrieve."

    openapi_schema["paths"]["/stories"]["get"]["summary"] = \
        "Search for stories matching a variety of criteria. Stories are sorted by their score (upvotes) by default."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][0]["description"] = \
        "Find stories submitted by this user."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][1]["description"] = \
        "Find stories submitted at or before this UNIX time."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][2]["description"] = \
        "Find stories submitted at or after this UNIX time."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][3]["description"] = \
        "Find stories with a score equal or higher than this number."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][4]["description"] = \
        "Find stories with a score equal or lower than this number."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][5]["description"] = \
        "Find stories with a number of comments equal or higher than this number."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][6]["description"] = \
        "Find stories with a number of comments equal or lower than this number."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][7]["description"] = \
        "Sort results by score (upvotes, default), descendants (number of comments), time (of submission)."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][8]["description"] = \
        "Sort results in descending (default) or ascending order of the sort_by parameter."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][9]["description"] = \
        "Offset the results returned, use to page through multiple results."
    openapi_schema["paths"]["/stories"]["get"]["parameters"][10]["description"] = \
        "Limit the number of results returned (default 10, max 50)."

    openapi_schema["paths"]["/comments"]["get"]["summary"] = \
        "Find comments matching a variety of criteria. Comments are sorted by the most recent ones by default."
    openapi_schema["paths"]["/comments"]["get"]["parameters"][0]["description"] = \
        "Find comments submitted by this user."
    openapi_schema["paths"]["/comments"]["get"]["parameters"][1]["description"] = \
        "Find comments submitted at or before this UNIX time."
    openapi_schema["paths"]["/comments"]["get"]["parameters"][2]["description"] = \
        "Find comments submitted at or after this UNIX time."
    openapi_schema["paths"]["/comments"]["get"]["parameters"][3]["description"] = \
        "Sort results by score (upvotes), descendants (number of comments), time (of submission, default)."
    openapi_schema["paths"]["/comments"]["get"]["parameters"][4]["description"] = \
        "Sort results in descending (default) or ascending order of the sort_by parameter."
    openapi_schema["paths"]["/comments"]["get"]["parameters"][5]["description"] = \
        "Offset the results returned, use to page through multiple results."
    openapi_schema["paths"]["/comments"]["get"]["parameters"][6]["description"] = \
        "Limit the number of results returned (default 10, max 50)."

    openapi_schema["paths"]["/polls"]["get"]["summary"] = \
        "Find polls matching a variety of criteria. Polls are sorted by their score (upvotes) by default."
    openapi_schema["paths"]["/polls"]["get"]["parameters"][0]["description"] = \
        "Find polls submitted by this user."
    openapi_schema["paths"]["/polls"]["get"]["parameters"][1]["description"] = \
        "Find polls submitted at or before this UNIX time."
    openapi_schema["paths"]["/polls"]["get"]["parameters"][2]["description"] = \
        "Find polls submitted at or after this UNIX time."
    openapi_schema["paths"]["/polls"]["get"]["parameters"][3]["description"] = \
        "Sort results by score (upvotes, default), descendants (number of comments), time (of submission)."
    openapi_schema["paths"]["/polls"]["get"]["parameters"][4]["description"] = \
        "Sort results in descending (default) or ascending order of the sort_by parameter."
    openapi_schema["paths"]["/polls"]["get"]["parameters"][5]["description"] = \
        "Offset the results returned, use to page through multiple results."
    openapi_schema["paths"]["/polls"]["get"]["parameters"][6]["description"] = \
        "Limit the number of results returned (default 10, max 50)."
    openapi_schema["paths"]["/polls"]["get"]["parameters"][7]["description"] = \
        "Search for polls whose title or text contains this phrase."

    openapi_schema["paths"]["/users"]["get"]["summary"] = \
        "Find users matching a variety of criteria. Users are sorted by their karma (upvotes) by default."
    openapi_schema["paths"]["/users"]["get"]["parameters"][0]["description"] = \
        "Find users created at or before this UNIX time."
    openapi_schema["paths"]["/users"]["get"]["parameters"][1]["description"] = \
        "Find users created at or after this UNIX time."
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
        "Use to ensure results are sorted by a specific value. Valid values are 'score', 'descendant', or 'time'."
    openapi_schema["components"]["schemas"]["SortOrder"]["description"] = \
        "Use to ensure results are sorted in a specific order. Valid values are 'desc' or 'asc'."
    openapi_schema["components"]["schemas"]["UserSortBy"]["description"] = \
        "Use to ensure results are sorted by a specific value. Valid values are 'karma' or 'created'."

    return openapi_schema
