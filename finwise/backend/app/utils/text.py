import re

_NOISE = {
    "UPI", "IMPS", "NEFT", "RTGS", "POS", "ECOM", "DR", "CR", "DEBIT", "CREDIT", "PAYMENT", "PAYMENTS", "TXN",
    "REF", "TRANSFER", "TO", "FROM", "BY", "THE", "ORDER", "BILL", "PAY", "ACH", "NACH", "MANDATE", "PURCHASE",
    "OKAXIS", "OKSBI", "OKICICI", "OKHDFCBANK", "YBL", "PAYTM", "IBL", "AXL", "APL", "UPIID", "VPA", "ON", "AT",
    "PVT", "LTD", "LIMITED", "INDIA", "SERVICES", "TECHNOLOGIES", "COM", "WWW", "INR", "RS", "MOBILE", "BANKING",
    "D", "C", "TRF", "CHQ", "CHEQUE", "AUTOPAY", "AUTO", "DEBITED", "CREDITED",
}


def clean_description(raw: str) -> str:
    s = re.sub(r"[\r\n\t]+", " ", raw or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:500]


def extract_merchant(raw: str) -> str:
    """Heuristic merchant key: first <=2 meaningful tokens of the description (upper-case)."""
    s = (raw or "").upper()
    s = re.sub(r"\.(COM|IN|CO)\b", " ", s)
    s = re.sub(r"[/@|*\-_:,;()\[\]]", " ", s)
    tokens = []
    for t in s.split():
        if t in _NOISE:
            continue
        if sum(c.isdigit() for c in t) >= 4:      # reference numbers / account numbers
            continue
        if not re.search(r"[A-Z]", t):
            continue
        tokens.append(re.sub(r"[^A-Z0-9&]", "", t))
    tokens = [t for t in tokens if t]
    return " ".join(tokens[:2])[:120] or "UNKNOWN"
