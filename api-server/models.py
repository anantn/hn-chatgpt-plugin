from sqlalchemy import Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    created = Column(Integer)
    karma = Column(Integer)
    about = Column(String)
    submitted = Column(String)

    items = relationship("Item", back_populates="author")


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    title = Column(String)
    url = Column(String)
    text = Column(String)
    score = Column(Integer)
    by = Column(Integer, ForeignKey("users.id"))

    author = relationship("User", back_populates="items")