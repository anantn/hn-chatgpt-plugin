from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, asc
from typing import Union, Optional
import models
import schemas


def get_user(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.id == user_id).first()


def get_users(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.User).offset(skip).limit(limit).all()


def get_items(db: Session, skip: int = 0, limit: int = 100,
              type: Union[models.ItemType, None] = None,
              query: Optional[str] = None,
              by: Optional[str] = None,
              sort_by: Optional[models.SortBy] = None,
              order: models.Order = models.Order.desc,
              fields = []):
    # TODO(ruravi): IN clause.
    db_query = db.query(models.Item, *fields)
    if type is not None:
        db_query = db_query.filter(models.Item.type == type)
    if query is not None:
        db_query = db_query.filter(
            or_(models.Item.title.contains(query), models.Item.text.contains(query)))
    if by is not None:
        db_query = db_query.filter(models.Item.by == by)
    if sort_by is not None:
        field = models.Item.score if sort_by == models.SortBy.score else models.Item.time
        db_query = db_query.order_by(desc(
            field)) if order == models.Order.desc else db_query.order_by(asc(field))
    else:
        # Default sort order is descending order of score
        db_query = db_query.order_by(desc(models.SortBy.score.name))
    return db_query.offset(skip).limit(limit).all()


def get_item(db: Session, item_id: int):
    return db.query(models.Item).filter(models.Item.id == item_id).first()
