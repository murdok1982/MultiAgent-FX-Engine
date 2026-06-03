"""
royalty/royalty.py — Transparent 10% Royalty Module (Commercial License)
========================================================================

Per LICENSE-COMMERCIAL.md, every closed trade with NET PROFIT > 0 owes
10% of that profit to the Author. This module:

  1. Holds the public payout addresses (AUTHOR_WALLETS) — visible, auditable.
  2. Calculates the royalty amount per trade.
  3. Queues a payment record in the DB (RoyaltyObligation table).
  4. In LIVE mode with a user-provided private key, signs and broadcasts
     an on-chain transfer using the chain selected via ROYALTY_PREFERRED_CHAIN.
  5. In PAPER mode, the royalty is logged but no real transfer occurs.

THIS FILE IS SUBJECT TO INTEGRITY VERIFICATION (royalty/integrity.py).
Removing it, monkey-patching it, or blocking its execution breaches
the commercial license and disables LIVE mode.

Author wallets (verifiable on-chain):
  ETH / Linea / Base / BNB / Polygon:  0x2720705E09a049F2F029090292e4626b72fCf4F9
  Bitcoin (native segwit):              bc1qdjg5sa7hnn0hcktxeexp6sscaktwlx8ql0cd53
  Solana:                               CTFUHHjbjZWCgki4EhmeNvxENHVzN1sYm64DPFKoUoH6
  TRON:                                 TG7rBDuf8uCawAwLdPknZuKtNgoGW6wQaP
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Dict, Any

# ── Public Constants (auditable) ──────────────────────────────────────────────

ROYALTY_PCT: float = 10.0  # 10% of NET PROFIT on winning trades only

AUTHOR_WALLETS: Dict[str, str] = {
    # EVM-compatible chains (share address)
    "ethereum":  "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    "linea":     "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    "base":      "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    "bnb":       "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    "polygon":   "0x2720705E09a049F2F029090292e4626b72fCf4F9",
    # Non-EVM
    "bitcoin":   "bc1qdjg5sa7hnn0hcktxeexp6sscaktwlx8ql0cd53",
    "solana":    "CTFUHHjbjZWCgki4EhmeNvxENHVzN1sYm64DPFKoUoH6",
    "tron":      "TG7rBDuf8uCawAwLdPknZuKtNgoGW6wQaP",
}

EVM_CHAINS = {"ethereum", "linea", "base", "bnb", "polygon"}

# Default RPCs (user can override via env)
DEFAULT_RPCS: Dict[str, str] = {
    "ethereum": "https://eth.llamarpc.com",
    "linea":    "https://rpc.linea.build",
    "base":     "https://mainnet.base.org",
    "bnb":      "https://bsc-dataseed.binance.org",
    "polygon":  "https://polygon-rpc.com",
}

CHAIN_IDS: Dict[str, int] = {
    "ethereum": 1,
    "linea":    59144,
    "base":     8453,
    "bnb":      56,
    "polygon":  137,
}


# ── Domain Types ──────────────────────────────────────────────────────────────

@dataclass
class RoyaltyResult:
    pnl_net: float
    royalty_amount: float          # in account currency
    chain: str
    wallet: str
    transferred: bool
    tx_hash: Optional[str]
    error: Optional[str]
    timestamp: str


# ── Calculation ───────────────────────────────────────────────────────────────

def calculate_royalty(net_pnl: float) -> float:
    """
    Returns the royalty owed for a single closed trade.

    Rule (per LICENSE-COMMERCIAL.md §3):
      - net_pnl <= 0  → no royalty
      - net_pnl >  0  → 10% of net_pnl, rounded DOWN to 2 decimals
    """
    if net_pnl is None or net_pnl <= 0:
        return 0.0
    amount = Decimal(str(net_pnl)) * Decimal("0.10")
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


# ── Disclosure (printed on import in LIVE mode) ───────────────────────────────

_DISCLOSURE_PRINTED = False

def royalty_disclosure() -> None:
    """Print a one-time disclosure of the active royalty mechanism."""
    global _DISCLOSURE_PRINTED
    if _DISCLOSURE_PRINTED:
        return
    _DISCLOSURE_PRINTED = True
    chain = os.getenv("ROYALTY_PREFERRED_CHAIN", "polygon").lower()
    wallet = AUTHOR_WALLETS.get(chain, AUTHOR_WALLETS["polygon"])
    print(
        "\n"
        "╔══════════════════════════════════════════════════════════════════╗\n"
        "║  COMMERCIAL LICENSE — ROYALTY DISCLOSURE                         ║\n"
        "║                                                                  ║\n"
        "║  This software charges 10% of NET PROFIT per winning trade.      ║\n"
        "║  Losing trades pay nothing. PAPER mode pays nothing.             ║\n"
        "║                                                                  ║\n"
        f"║  Active chain:  {chain:<48} ║\n"
        f"║  Author wallet: {wallet[:48]:<48} ║\n"
        "║                                                                  ║\n"
        "║  See LICENSE-COMMERCIAL.md for full terms.                       ║\n"
        "╚══════════════════════════════════════════════════════════════════╝\n"
    )


# ── DB Persistence (always, both PAPER and LIVE) ──────────────────────────────

def queue_royalty_payment(
    trade_id: str,
    net_pnl: float,
    chain: str,
    wallet: str,
    royalty_amount: float,
    transferred: bool,
    tx_hash: Optional[str],
    error: Optional[str],
) -> None:
    """Persist royalty obligation/payment to DB (RoyaltyObligation table)."""
    try:
        from database import get_session, RoyaltyObligation
        with get_session() as s:
            s.add(RoyaltyObligation(
                trade_id=trade_id,
                net_pnl=net_pnl,
                royalty_amount=royalty_amount,
                chain=chain,
                wallet=wallet,
                transferred=transferred,
                tx_hash=tx_hash,
                error=error,
            ))
            s.commit()
    except Exception as e:
        # Persistence failure must never break the trading loop
        try:
            from utils.logger import get_logger
            get_logger("royalty").error(f"Royalty DB persistence failed: {e}")
        except Exception:
            pass


# ── On-Chain Transfer (LIVE only, user-funded) ────────────────────────────────

def _transfer_evm(
    chain: str,
    amount_native: float,
    user_priv_key: str,
    user_address: str,
    rpc_url: str,
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Send a native-token transfer on an EVM chain.

    Returns (success, tx_hash, error). Uses web3.py if installed.
    The User's private key is read from env (NEVER stored or sent anywhere).
    """
    try:
        from web3 import Web3
    except ImportError:
        return False, None, "web3 library not installed (pip install web3)"

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        if not w3.is_connected():
            return False, None, f"RPC unreachable: {rpc_url}"

        to_addr = Web3.to_checksum_address(AUTHOR_WALLETS[chain])
        from_addr = Web3.to_checksum_address(user_address)
        chain_id = CHAIN_IDS[chain]

        nonce = w3.eth.get_transaction_count(from_addr)
        value_wei = w3.to_wei(amount_native, "ether")
        gas_price = w3.eth.gas_price

        tx = {
            "from": from_addr,
            "to": to_addr,
            "value": value_wei,
            "nonce": nonce,
            "gas": 21000,
            "gasPrice": gas_price,
            "chainId": chain_id,
        }
        signed = w3.eth.account.sign_transaction(tx, user_priv_key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        return True, tx_hash.hex(), None
    except Exception as e:
        return False, None, f"EVM transfer failed: {e}"


def transfer_royalty_live(
    trade_id: str,
    net_pnl: float,
    quote_rate_usd: float = 1.0,
) -> RoyaltyResult:
    """
    Execute the royalty transfer in LIVE mode.

    Reads from env:
        ROYALTY_PREFERRED_CHAIN  — chain to use (default: polygon)
        ROYALTY_USER_PRIV_KEY    — user's signing key (LIVE only)
        ROYALTY_USER_ADDRESS     — user's source address (LIVE only)
        ROYALTY_<CHAIN>_RPC      — optional RPC override
        ROYALTY_NATIVE_PER_USD_<CHAIN> — fx rate from account currency to native token

    For non-EVM chains, this function only LOGS the obligation: BTC/SOL/TRON
    transfers require separate signing infrastructure the User must
    integrate. The obligation is still recorded in the DB.
    """
    chain = os.getenv("ROYALTY_PREFERRED_CHAIN", "polygon").lower()
    wallet = AUTHOR_WALLETS.get(chain)
    if wallet is None:
        return RoyaltyResult(
            pnl_net=net_pnl, royalty_amount=0.0, chain=chain, wallet="",
            transferred=False, tx_hash=None,
            error=f"Unknown chain '{chain}'",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    amount_quote = calculate_royalty(net_pnl)  # in account currency (e.g. USD)
    if amount_quote <= 0:
        return RoyaltyResult(
            pnl_net=net_pnl, royalty_amount=0.0, chain=chain, wallet=wallet,
            transferred=False, tx_hash=None, error=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # Non-EVM: log only, manual settlement
    if chain not in EVM_CHAINS:
        queue_royalty_payment(
            trade_id, net_pnl, chain, wallet, amount_quote,
            transferred=False, tx_hash=None,
            error="Non-EVM chain — manual settlement required by User",
        )
        return RoyaltyResult(
            pnl_net=net_pnl, royalty_amount=amount_quote,
            chain=chain, wallet=wallet, transferred=False, tx_hash=None,
            error="Non-EVM chain — manual settlement",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    priv_key = os.getenv("ROYALTY_USER_PRIV_KEY", "").strip()
    user_addr = os.getenv("ROYALTY_USER_ADDRESS", "").strip()
    rpc_env = f"ROYALTY_{chain.upper()}_RPC"
    rpc_url = os.getenv(rpc_env, DEFAULT_RPCS[chain])

    if not priv_key or not user_addr:
        queue_royalty_payment(
            trade_id, net_pnl, chain, wallet, amount_quote,
            transferred=False, tx_hash=None,
            error="ROYALTY_USER_PRIV_KEY / ROYALTY_USER_ADDRESS not set",
        )
        return RoyaltyResult(
            pnl_net=net_pnl, royalty_amount=amount_quote,
            chain=chain, wallet=wallet, transferred=False, tx_hash=None,
            error="No signing key configured — obligation logged only",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # Convert quote-currency royalty into native token amount
    fx_env = f"ROYALTY_NATIVE_PER_USD_{chain.upper()}"
    native_per_quote = float(os.getenv(fx_env, "0") or 0)
    if native_per_quote <= 0:
        queue_royalty_payment(
            trade_id, net_pnl, chain, wallet, amount_quote,
            transferred=False, tx_hash=None,
            error=f"{fx_env} not set — cannot price native amount",
        )
        return RoyaltyResult(
            pnl_net=net_pnl, royalty_amount=amount_quote,
            chain=chain, wallet=wallet, transferred=False, tx_hash=None,
            error=f"{fx_env} not configured",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    native_amount = amount_quote * native_per_quote * quote_rate_usd

    ok, tx_hash, err = _transfer_evm(
        chain, native_amount, priv_key, user_addr, rpc_url
    )
    queue_royalty_payment(
        trade_id, net_pnl, chain, wallet, amount_quote,
        transferred=ok, tx_hash=tx_hash, error=err,
    )
    return RoyaltyResult(
        pnl_net=net_pnl, royalty_amount=amount_quote,
        chain=chain, wallet=wallet, transferred=ok, tx_hash=tx_hash,
        error=err,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def process_trade_close(
    trade_id: str,
    net_pnl: float,
    trading_mode: str = "PAPER",
) -> Optional[RoyaltyResult]:
    """
    Called by the trading engine after EVERY closed trade.

    PAPER mode → logs the would-be royalty without transferring.
    LIVE mode  → attempts transfer + logs the result.
    Losses     → no royalty, returns None.
    """
    if net_pnl is None or net_pnl <= 0:
        return None

    if trading_mode.upper() == "PAPER":
        amount = calculate_royalty(net_pnl)
        chain = os.getenv("ROYALTY_PREFERRED_CHAIN", "polygon").lower()
        wallet = AUTHOR_WALLETS.get(chain, "")
        queue_royalty_payment(
            trade_id=trade_id, net_pnl=net_pnl, chain=chain, wallet=wallet,
            royalty_amount=amount, transferred=False, tx_hash=None,
            error="PAPER mode — simulated obligation only",
        )
        return RoyaltyResult(
            pnl_net=net_pnl, royalty_amount=amount,
            chain=chain, wallet=wallet, transferred=False, tx_hash=None,
            error="PAPER mode",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    return transfer_royalty_live(trade_id, net_pnl)
