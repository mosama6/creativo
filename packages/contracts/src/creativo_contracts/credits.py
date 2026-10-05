from datetime import datetime

from pydantic import BaseModel

from creativo_contracts.enums import CreditTransactionType


class CreditAccountView(BaseModel):
    available: int
    reserved: int


class CreditTransactionView(BaseModel):
    id: str
    type: CreditTransactionType
    amount: int
    available_after: int
    reserved_after: int
    description: str
    generation_id: str | None
    created_at: datetime


class CreditTransactionPage(BaseModel):
    items: list[CreditTransactionView]
