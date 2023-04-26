from pydantic import BaseModel
from typing import Optional

class ItemBase(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None
    by: str

class UserBase(BaseModel):
    id: str
    created: int
    karma: int
    about: str
    submitted: str

class Item(ItemBase):
    id: int
    class Config:
        orm_mode = True

class User(UserBase):
    items: list[Item] = []

    class Config:
        orm_mode = True