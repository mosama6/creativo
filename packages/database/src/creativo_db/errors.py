class LedgerError(Exception):
    code = "ledger_error"


class InsufficientCredits(LedgerError):
    code = "insufficient_credits"
