"""The only place credit balances change.

Balances move with a conditional UPDATE plus an append-only ledger row.
Reservation, consumption, and refund are idempotent per generation.
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from creativo_common.ids import new_id
from creativo_common.timeutil import utcnow
from creativo_db.errors import InsufficientCredits, LedgerError
from creativo_db.models import CreditAccount, CreditTransaction


async def ensure_account(session: AsyncSession, user_id: str) -> CreditAccount:
    account = await session.get(CreditAccount, user_id)
    if account is None:
        account = CreditAccount(user_id=user_id, available=0, reserved=0)
        session.add(account)
        await session.flush()
    return account


async def _find(
    session: AsyncSession, generation_id: str, tx_type: str
) -> CreditTransaction | None:
    return await session.scalar(
        select(CreditTransaction).where(
            CreditTransaction.generation_id == generation_id,
            CreditTransaction.type == tx_type,
        )
    )


async def grant(
    session: AsyncSession,
    *,
    user_id: str,
    amount: int,
    tx_type: str,
    description: str,
    generation_id: str | None = None,
) -> CreditTransaction:
    if amount <= 0:
        raise LedgerError("grant amount must be positive")
    if tx_type not in {"purchase", "bonus", "admin_adjustment"}:
        raise LedgerError("unsupported grant type")
    await ensure_account(session, user_id)
    row = (
        await session.execute(
            update(CreditAccount)
            .where(CreditAccount.user_id == user_id)
            .values(available=CreditAccount.available + amount)
            .returning(CreditAccount.available, CreditAccount.reserved)
        )
    ).one()
    tx = CreditTransaction(
        id=new_id("txn"),
        user_id=user_id,
        generation_id=generation_id,
        type=tx_type,
        amount=amount,
        available_after=row.available,
        reserved_after=row.reserved,
        description=description,
        created_at=utcnow(),
    )
    session.add(tx)
    await session.flush()
    return tx


async def reserve(
    session: AsyncSession,
    *,
    user_id: str,
    generation_id: str,
    amount: int,
) -> CreditTransaction:
    if amount <= 0:
        raise LedgerError("reservation amount must be positive")
    existing = await _find(session, generation_id, "reservation")
    if existing is not None:
        return existing
    await ensure_account(session, user_id)
    row = (
        await session.execute(
            update(CreditAccount)
            .where(CreditAccount.user_id == user_id, CreditAccount.available >= amount)
            .values(
                available=CreditAccount.available - amount,
                reserved=CreditAccount.reserved + amount,
            )
            .returning(CreditAccount.available, CreditAccount.reserved)
        )
    ).first()
    if row is None:
        raise InsufficientCredits("Not enough available credits for this generation.")
    tx = CreditTransaction(
        id=new_id("txn"),
        user_id=user_id,
        generation_id=generation_id,
        type="reservation",
        amount=amount,
        available_after=row.available,
        reserved_after=row.reserved,
        description="Reserved for generation",
        created_at=utcnow(),
    )
    session.add(tx)
    await session.flush()
    return tx


async def consume(
    session: AsyncSession, *, user_id: str, generation_id: str
) -> CreditTransaction | None:
    existing = await _find(session, generation_id, "consumption")
    if existing is not None:
        return existing
    reservation = await _find(session, generation_id, "reservation")
    if reservation is None:
        return None
    row = (
        await session.execute(
            update(CreditAccount)
            .where(
                CreditAccount.user_id == user_id,
                CreditAccount.reserved >= reservation.amount,
            )
            .values(reserved=CreditAccount.reserved - reservation.amount)
            .returning(CreditAccount.available, CreditAccount.reserved)
        )
    ).first()
    if row is None:
        raise LedgerError("reserved balance missing for consumption")
    tx = CreditTransaction(
        id=new_id("txn"),
        user_id=user_id,
        generation_id=generation_id,
        type="consumption",
        amount=reservation.amount,
        available_after=row.available,
        reserved_after=row.reserved,
        description="Consumed for generation",
        created_at=utcnow(),
    )
    session.add(tx)
    await session.flush()
    return tx


async def refund(
    session: AsyncSession, *, user_id: str, generation_id: str, description: str
) -> CreditTransaction | None:
    existing = await _find(session, generation_id, "refund")
    if existing is not None:
        return existing
    consumed = await _find(session, generation_id, "consumption")
    if consumed is not None:
        return None
    reservation = await _find(session, generation_id, "reservation")
    if reservation is None:
        return None
    row = (
        await session.execute(
            update(CreditAccount)
            .where(
                CreditAccount.user_id == user_id,
                CreditAccount.reserved >= reservation.amount,
            )
            .values(
                reserved=CreditAccount.reserved - reservation.amount,
                available=CreditAccount.available + reservation.amount,
            )
            .returning(CreditAccount.available, CreditAccount.reserved)
        )
    ).first()
    if row is None:
        raise LedgerError("reserved balance missing for refund")
    tx = CreditTransaction(
        id=new_id("txn"),
        user_id=user_id,
        generation_id=generation_id,
        type="refund",
        amount=reservation.amount,
        available_after=row.available,
        reserved_after=row.reserved,
        description=description,
        created_at=utcnow(),
    )
    session.add(tx)
    await session.flush()
    return tx
