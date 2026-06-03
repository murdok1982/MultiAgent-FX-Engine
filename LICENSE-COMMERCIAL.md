# MultiAgent FX Engine — Commercial License v1.0

**Copyright (c) 2026 Gustavo Lobato Clara (gustavolobatoclara@gmail.com)**
**All rights reserved.**

This file supersedes the previous MIT license for any use of this software
in connection with real-money trading ("LIVE mode"). Paper / simulated /
educational use remains free under the terms described in Section 7.

---

## 1. Definitions

- **"Software"** — the MultiAgent FX Engine source code, models, prompts,
  documentation, and all associated files in this repository.
- **"Author"** — Gustavo Lobato Clara, sole copyright holder.
- **"User"** — any natural or legal person who downloads, installs, runs,
  modifies or distributes the Software.
- **"LIVE mode"** — any operation of the Software that places real orders
  on a real (non-demo) brokerage account, regardless of broker, currency
  pair, or notional size.
- **"Net Profit"** — the realized PnL of a closed trade, after subtracting
  spread, commission and broker fees, expressed in account currency.
- **"Royalty"** — the fee defined in Section 3.

---

## 2. License Grant

Subject to full compliance with this license, the Author grants the User
a non-exclusive, non-transferable, revocable license to:

a) Install and run the Software in LIVE mode on the User's own broker
   account(s).
b) Modify the Software for the User's internal use only.
c) Use the Software's signals and decisions to inform real-money trades.

The license does **NOT** grant the right to:

- Redistribute LIVE-mode-capable builds to third parties.
- Sublicense, rent, lease or resell the Software.
- Remove, bypass, obfuscate or otherwise tamper with the royalty
  collection mechanism (see Section 4).

---

## 3. Royalty

For every closed trade with **Net Profit > 0**, the User agrees to remit
**ten percent (10%) of Net Profit** to the Author, via one of the
wallet addresses listed in `royalty/royalty.py` (`AUTHOR_WALLETS`).

- Losing trades trigger **no royalty obligation**.
- The royalty is auto-calculated and queued by the `royalty` module.
- The User is responsible for funding gas / network fees from their own
  wallet. The Software never requests, stores, or transmits the
  Author's private keys.
- The User may pre-select a preferred chain via the
  `ROYALTY_PREFERRED_CHAIN` environment variable.

---

## 4. Anti-Tampering Clause

The royalty mechanism is implemented in `royalty/royalty.py` and verified
by `royalty/integrity.py` using SHA-256 checksums on system start.

a) **Any modification, removal, bypass, monkey-patch, network block or
   reverse-engineering of either file** is a material breach of this
   license and immediately terminates the User's right to operate the
   Software in LIVE mode.
b) The Software is engineered to refuse to enter LIVE mode if integrity
   verification fails. Attempting to bypass this check is also a breach.
c) Breach may give rise to claims for the full economic value of the
   royalty that would have been due, plus damages, plus reasonable legal
   costs, under the jurisdiction of the Author's residence.

---

## 5. Commercial License Key

LIVE mode requires a valid `COMMERCIAL_LICENSE_KEY` set in `.env`.
The User must obtain the key directly from the Author via
gustavolobatoclara@gmail.com.

- License keys are personal and non-transferable.
- One key per natural-person User or per legal entity.
- The Author may revoke a key in case of breach.

Without a valid key, the Software falls back to PAPER mode regardless
of CLI arguments.

---

## 6. Warranty Disclaimer

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT.

**Trading Forex involves a high risk of loss. Past results, paper or
backtested, do not predict future returns. The User accepts full
responsibility for all losses.** In no event shall the Author be liable
for any claim, damages, or other liability arising from, out of or in
connection with the Software.

---

## 7. PAPER Mode — Free Use

Use of the Software in PAPER mode (simulated trading only, no real money)
remains **free** under the original MIT terms in `LICENSE` (file kept in
the repository for backward compatibility).

PAPER mode never triggers any royalty obligation.

---

## 8. Termination

This license terminates automatically upon any breach. Upon termination
the User must immediately stop running the Software in LIVE mode and
delete the LIVE configuration. The royalty obligation accrued before
termination survives.

---

## 9. Governing Law

This license is governed by the laws of Spain. Any dispute shall be
resolved in the courts of Madrid, Spain, unless the Author and User
agree otherwise in writing.

---

**By running this Software in LIVE mode you acknowledge that you have
read, understood and accepted these terms.**

— Gustavo Lobato Clara, 2026
