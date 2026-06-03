"""
tools/pin_hash.py — Re-pin the integrity hash after legitimate changes.

Run by the Author only, after modifying royalty/royalty.py.
Prints the new SHA-256 to paste into royalty/integrity.py.
"""
import hashlib
from pathlib import Path

target = Path(__file__).resolve().parent.parent / "royalty" / "royalty.py"
h = hashlib.sha256()
with open(target, "rb") as f:
    for chunk in iter(lambda: f.read(8192), b""):
        h.update(chunk)
new_hash = h.hexdigest()
print(f"New SHA-256 of royalty/royalty.py:")
print(f"  {new_hash}")
print(f"\nReplace REFERENCE_HASH in royalty/integrity.py with the value above.")
