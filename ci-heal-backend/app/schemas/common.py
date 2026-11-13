"""Shapes shared by more than one router."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """One page of a list endpoint, plus the total number of matching rows."""

    items: list[T]
    total: int
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
