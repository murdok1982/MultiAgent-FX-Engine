"""
royalty/integrity.py — Anti-Tampering Verification
==================================================

Enforces LICENSE-COMMERCIAL.md §4: if `royalty/royalty.py` is modified,
removed, or monkey-patched, LIVE mode is refused.

This is NOT obfuscation — the mechanism is fully documented. It is a
license-gating control: keep the royalty module intact, or the system
will not place real trades.

How it works:
  1. The expected SHA-256 of `royalty/royalty.py` is hardcoded below
     (REFERENCE_HASH), pinned at release time by `tools/pin_hash.py`.
  2. On every system start in LIVE mode, we recompute the SHA-256 of
     the current file and compare.
  3. We also validate that AUTHOR_WALLETS still contains the published
     payout addresses (defense-in-depth: prevents a SHA-collision attack
     via address swap).
  4. On mismatch we raise IntegrityError, which `main.py` catches to
     refuse LIVE mode (PAPER mode keeps working).

To re-pin after legitimate changes by the Author, run:
    python tools/pin_hash.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Dict


# ── Reference SHA-256 of royalty/royalty.py at release time ───────────────────
# Recompute with: python tools/pin_hash.py
REFERENCE_HASH: str = "6701f226756ab71c78df1e30c7c35d087273686af121cff7965a019f49ade4c5"

# Expected author payout addresses (defense-in-depth)
EXPECTED_WALLETS: Dict[str, str] = {
    "ethereum":  "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    "linea":     "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    "base":      "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    "bnb":       "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    "polygon":   "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    "bitcoin":   "bc1qdjg5sa7hnn0hcktxeexp6sscaktwlx8ql0cd53",
    "solana":    "CTFUHHjbjZWCgki4EhmeNvxENHVzN1sYm64DPFKoUoH6",
    "tron":      "TG7rBDuf8uCawAwLdPknZuKtNgoGW6wQaP",
}


class IntegrityError(Exception):
    """Raised when royalty module integrity check fails."""
    pass


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_integrity(strict: bool = True) -> bool:
    """
    Verify that royalty/royalty.py has not been tampered with.

    Args:
        strict: If True (default), raise IntegrityError on mismatch.
                If False, return False on mismatch.

    Returns:
        True if integrity is intact.
    """
    royalty_path = Path(__file__).parent / "royalty.py"

    # 1. File exists
    if not royalty_path.exists():
        msg = (
            "royalty/royalty.py is missing. Live trading is disabled. "
            "Restore the file from the official repository."
        )
        if strict:
            raise IntegrityError(msg)
        print(f"[INTEGRITY] {msg}", file=sys.stderr)
        return False

    # 2. SHA-256 matches
    actual_hash = _hash_file(royalty_path)
    if actual_hash != REFERENCE_HASH:
        msg = (
            f"royalty/royalty.py has been modified.\n"
            f"  Expected SHA-256: {REFERENCE_HASH}\n"
            f"  Actual   SHA-256: {actual_hash}\n"
            f"Per LICENSE-COMMERCIAL.md §4, LIVE mode is disabled. "
            f"Restore the original file or re-pin via tools/pin_hash.py "
            f"(authorized author only)."
        )
        if strict:
            raise IntegrityError(msg)
        print(f"[INTEGRITY] {msg}", file=sys.stderr)
        return False

    # 3. Wallets still match (defense-in-depth)
    try:
        from royalty.royalty import AUTHOR_WALLETS
    except Exception as e:
        msg = f"royalty/royalty.py is importable but corrupted: {e}"
        if strict:
            raise IntegrityError(msg)
        print(f"[INTEGRITY] {msg}", file=sys.stderr)
        return False

    for chain, expected_addr in EXPECTED_WALLETS.items():
        actual = AUTHOR_WALLETS.get(chain)
        if actual != expected_addr:
            msg = (
                f"AUTHOR_WALLETS['{chain}'] tampered. "
                f"Expected={expected_addr!r}, got={actual!r}. "
                f"LIVE mode disabled."
            )
            if strict:
                raise IntegrityError(msg)
            print(f"[INTEGRITY] {msg}", file=sys.stderr)
            return False

    return True


def can_run_live() -> tuple[bool, str]:
    """
    Convenience wrapper used by main.py to decide whether to allow LIVE.

    Returns (allowed, reason). When allowed is False, reason explains why.
    """
    try:
        verify_integrity(strict=True)
        return True, "OK"
    except IntegrityError as e:
        return False, str(e)
