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


@app.get("/items/{item_id}", response_model=schemas.Item)
async def read_item(item_id: int, q: Union[str, None] = None, db: Session = Depends(get_db)):
    return crud.get_item(db, item_id)


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
