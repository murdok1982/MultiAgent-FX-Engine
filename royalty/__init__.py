"""
royalty/ — Commercial License Royalty Module
============================================

This package implements the 10% royalty mechanism required by the
Commercial License (see LICENSE-COMMERCIAL.md).

Public API:
    AUTHOR_WALLETS         — dict of author payout addresses by chain
    calculate_royalty(pnl) — computes 10% royalty on positive net profit
    queue_royalty_payment  — records the obligation to DB
    verify_integrity()     — anti-tampering SHA-256 check
    royalty_disclosure()   — prints license disclosure on import

Tampering with this module disables LIVE mode. See integrity.py.
"""
from royalty.royalty import (
    AUTHOR_WALLETS,
    ROYALTY_PCT,
    calculate_royalty,
    queue_royalty_payment,
    royalty_disclosure,
)
from royalty.integrity import (
    verify_integrity,
    IntegrityError,
)

__all__ = [
    "AUTHOR_WALLETS",
    "ROYALTY_PCT",
    "calculate_royalty",
    "queue_royalty_payment",
    "royalty_disclosure",
    "verify_integrity",
    "IntegrityError",
]
