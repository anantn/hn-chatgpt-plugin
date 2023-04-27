from sqlalchemy import Column, ForeignKey, Integer, String, Enum, Table
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
import enum

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    created = Column(Integer)
    karma = Column(Integer)
    about = Column(String)
    submitted = Column(String)

    items = relationship("Item", back_populates="author")


class ItemType(enum.Enum):
    comment = 'comment'
    job = 'job'
    story = 'story'
    poll = 'poll'
    pollopt = 'pollopt'


class SortBy(enum.Enum):
    score = 'score'
    time = 'time'


class Order(enum.Enum):
    asc = 'asc'
    desc = 'desc'


# Define an assoc table
association_table = Table(
    "kids",
    Base.metadata,
    Column("item", Integer, ForeignKey("items.id")),
    Column("kid", Integer, ForeignKey("items.id")),
    Column("display_order", Integer)
)


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    time = Column(Integer)
    title = Column(String)
    url = Column(String)
    text = Column(String)
    score = Column(Integer)
    type = Column(Enum(ItemType))
    by = Column(Integer, ForeignKey("users.id"))

    author = relationship("User", back_populates="items")
    kids = relationship("Item", secondary=association_table,
                        primaryjoin=id == association_table.c.item,
                        secondaryjoin=id == association_table.c.kid,
                        order_by=association_table.c.display_order)
