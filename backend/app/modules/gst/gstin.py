"""GSTIN / PAN / state-code validation (structure + official mod-36 check digit)."""

from __future__ import annotations

import re

# GST state/UT codes (as used in GSTINs and place-of-supply). 97 = Other Territory.
STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def gstin_check_digit(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _CHARSET.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _CHARSET[(36 - total % 36) % 36]


def validate_gstin(value: str) -> str:
    """Return the normalised GSTIN or raise ``ValueError``."""
    v = value.strip().upper()
    if not _GSTIN_RE.match(v):
        raise ValueError("GSTIN must be 15 characters in the format 22AAAAA0000A1Z5")
    if v[:2] not in STATE_CODES:
        raise ValueError(f"GSTIN state code {v[:2]} is not a valid GST state code")
    if gstin_check_digit(v[:14]) != v[14]:
        raise ValueError("GSTIN check digit is invalid")
    return v


def validate_pan(value: str) -> str:
    v = value.strip().upper()
    if not _PAN_RE.match(v):
        raise ValueError("PAN must be 10 characters in the format AAAAA0000A")
    return v


def validate_state_code(value: str) -> str:
    v = value.strip().zfill(2)
    if v not in STATE_CODES:
        raise ValueError(f"{value} is not a valid GST state code")
    return v
