from typing import Union
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

import crud
import models
import schemas
from database import SessionLocal, engine

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

# Dependency


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
async def read_root():
    return {"Hello": "World"}


@app.get("/stories", response_model=list[schemas.Item])
def get_stories(by: Union[str, None] = None,
               sort_by: Union[models.SortBy, None] = None,
               order: Union[models.Order, None] = None,
               include_children: bool = True,
               skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    fields = [] # If include_children, return all default fields.
    if not include_children:
        # Otherwise specify just 4 fields to return
        fields = [models.Item.title, models.Item.text, models.Item.url, models.Item.by]
    items = crud.get_items(db, skip=skip, limit=limit,
                           type=models.ItemType.story, 
                           fields=fields,
                           by=by, sort_by=sort_by, order=order)
    return items

@app.get("/comments", response_model=list[schemas.Item])
def get_comments(by: Union[str, None] = None,
               sort_by: Union[models.SortBy, None] = None,
               order: Union[models.Order, None] = None,
               skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    items = crud.get_items(db, skip=skip, limit=limit,
                           type=models.ItemType.comment, by=by, sort_by=sort_by,
                           order=order)
    return items

@app.get("/polls", response_model=list[schemas.Item])
def get_polls(by: Union[str, None] = None,
               sort_by: Union[models.SortBy, None] = None,
               order: Union[models.Order, None] = None,
               skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    items = crud.get_items(db, skip=skip, limit=limit,
                           type=models.ItemType.poll, by=by, sort_by=sort_by,
                           order=order)
    return items



@app.get("/items/", response_model=list[schemas.Item])
def read_items(type: Union[models.ItemType, None] = None,
               query: Union[str, None] = None,
               by: Union[str, None] = None,
               sort_by: Union[models.SortBy, None] = None,
               order: Union[models.Order, None] = None,
               skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    items = crud.get_items(db, skip=skip, limit=limit,
                           type=type, query=query, by=by, sort_by=sort_by,
                           order=order)
    return items
