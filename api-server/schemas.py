from pydantic import BaseModel
from typing import Optional
from models import ItemType

class ItemBase(BaseModel):
    title: Optional[str] = None
    time: int = 0
    url: Optional[str] = None
    text : Optional[str] = None
    score: Optional[int]
    type: ItemType
    by: Optional[str] = None

class UserBase(BaseModel):
    id: str
    created: int
    karma: int
    about: str
    submitted: str

class Item(ItemBase):
    id: int
    kids: list['Item'] = []
    class Config:
        orm_mode = True

class User(UserBase):
    items: list[Item] = []

    class Config:
        orm_mode = True