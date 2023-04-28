import enum
import json
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Table
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from pydantic import BaseModel, Field, validator

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
    text: Optional[str]
    parent: int
    kids: list['CommentResponse'] = []

    class Config:
        orm_mode = True


class StoryResponse(ItemResponse):
    title: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    score: Optional[int] = 0
    descendants: Optional[int] = 0
    kids: list['CommentResponse'] = []

    class Config:
        orm_mode = True


class PollResponse(ItemResponse):
    title: str
    text: Optional[str]
    score: Optional[int] = 0
    descendants: Optional[int] = 0
    parts: Optional[List[dict]] = None

    @validator("parts", pre=True)
    def parse_submitted(cls, value: Optional[str]) -> Optional[List[dict]]:
        if value is not None:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return []
        return None

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
