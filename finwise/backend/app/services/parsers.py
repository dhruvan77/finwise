"""Extensible statement parser architecture (CSV / Excel / PDF + optional OCR)."""
from __future__ import annotations

import csv
import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

from app.core.config import get_settings
from app.core.errors import AppError


@dataclass
class RawTxn:
    date: date
    description: str
    amount: Decimal           # positive
    direction: str            # debit|credit
    balance: Decimal | None = None
    low_confidence_direction: bool = False


@dataclass
class ParseResult:
    txns: list[RawTxn] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_rows: int = 0


class ParseError(AppError):
    def __init__(self, message: str):
        super().__init__(422, "parse_error", message)


# ---------- normalisation helpers ----------
_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d-%b-%Y", "%d %b %Y",
                 "%d-%b-%y", "%d %b %y", "%d %B %Y", "%b %d, %Y", "%Y/%m/%d", "%d/%b/%Y", "%Y-%m-%d %H:%M:%S",
                 "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"]


def parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        raise ValueError("empty date")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {s!r}")


_AMT_STRIP = re.compile(r"(?i)(₹|rs\.?|inr|\s)")


def parse_amount(value) -> tuple[Decimal, str | None]:
    """Return (signed Decimal, dr/cr suffix or None). Raises ValueError."""
    if value is None:
        raise ValueError("empty amount")
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and value != value:
            raise ValueError("empty amount")
        return Decimal(str(value)), None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "-"}:
        raise ValueError("empty amount")
    suffix = None
    m = re.search(r"(?i)\b(dr|cr)\.?$", s)
    if m:
        suffix, s = m.group(1).lower(), s[: m.start()]
    neg = s.strip().startswith("(") and s.strip().endswith(")")
    s = _AMT_STRIP.sub("", s).replace(",", "").strip("()")
    if s.endswith("-"):
        neg, s = True, s[:-1]
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"invalid amount {value!r}")
    return (-d if neg else d), suffix


# ---------- base ----------
class StatementParser(ABC):
    name = "base"
    source_type = "csv"

    @abstractmethod
    def parse(self, content: bytes) -> ParseResult: ...


PARSERS: dict[str, StatementParser] = {}


def register_parser(p: StatementParser) -> None:
    PARSERS[p.source_type] = p


def get_parser(source_type: str) -> StatementParser:
    return PARSERS[source_type]


# ---------- tabular (CSV / Excel) ----------
ALIASES = {
    "date": ["date", "txn date", "transaction date", "value date", "posting date", "tran date", "trans date", "booking date"],
    "description": ["description", "narration", "particulars", "details", "remarks", "transaction remarks",
                    "transaction description", "transaction details", "memo"],
    "debit": ["debit", "withdrawal", "withdrawal amt.", "withdrawal amount", "withdrawals", "dr", "debit amount", "paid out", "debit amt"],
    "credit": ["credit", "deposit", "deposit amt.", "deposit amount", "deposits", "cr", "credit amount", "paid in", "credit amt"],
    "amount": ["amount", "transaction amount", "amt", "txn amount"],
    "type": ["type", "dr/cr", "cr/dr", "transaction type", "txn type", "debit/credit"],
    "balance": ["balance", "closing balance", "running balance", "available balance", "bal"],
}


def _norm(h) -> str:
    return re.sub(r"\s+", " ", str(h).strip().lower())


def _map_columns(header: list) -> dict[str, int]:
    norm = [_norm(h) for h in header]
    mapping: dict[str, int] = {}
    for key, aliases in ALIASES.items():
        for a in aliases:
            if a in norm:
                mapping[key] = norm.index(a)
                break
    return mapping


def _find_header(rows: list[list]) -> tuple[int, dict[str, int]]:
    for i, row in enumerate(rows[:40]):
        m = _map_columns(row)
        if "date" in m and "description" in m and (("debit" in m or "credit" in m) or "amount" in m):
            return i, m
    raise ParseError("Could not find a header row with date, description and amount/debit/credit columns.")


def _rows_to_result(rows: list[list]) -> ParseResult:
    h, m = _find_header(rows)
    res = ParseResult()
    for idx, row in enumerate(rows[h + 1:], start=h + 2):
        if not any(str(c).strip() and str(c).lower() != "nan" for c in row):
            continue
        res.total_rows += 1

        def cell(k):
            i = m.get(k)
            return row[i] if i is not None and i < len(row) else None

        try:
            d = parse_date(cell("date"))
            desc = str(cell("description") or "").strip()
            if not desc or desc.lower() == "nan":
                raise ValueError("missing description")
            amount, direction, low = None, None, False
            debit = credit = None
            for k in ("debit", "credit"):
                try:
                    v, _ = parse_amount(cell(k))
                    if v != 0:
                        if k == "debit":
                            debit = abs(v)
                        else:
                            credit = abs(v)
                except ValueError:
                    pass
            if debit and credit:
                raise ValueError("both debit and credit present")
            if debit:
                amount, direction = debit, "debit"
            elif credit:
                amount, direction = credit, "credit"
            elif "amount" in m:
                v, suf = parse_amount(cell("amount"))
                t = str(cell("type") or "").strip().lower()
                if t in {"dr", "debit", "d", "withdrawal"} or suf == "dr":
                    direction = "debit"
                elif t in {"cr", "credit", "c", "deposit"} or suf == "cr":
                    direction = "credit"
                elif v < 0:
                    direction = "debit"
                elif "type" not in m and v > 0:
                    direction, low = "credit", True
                else:
                    raise ValueError("cannot determine debit/credit")
                amount = abs(v)
            if amount is None or amount == 0:
                raise ValueError("zero or missing amount")
            bal = None
            try:
                bal = parse_amount(cell("balance"))[0] if "balance" in m else None
            except ValueError:
                bal = None
            res.txns.append(RawTxn(d, desc, amount, direction, bal, low))
        except ValueError as e:
            res.errors.append({"row": idx, "error": str(e)})
    if any(t.low_confidence_direction for t in res.txns):
        res.warnings.append("Some rows have an unsigned 'amount' column without a type column; they were assumed to be credits. Please review.")
    if not res.txns and not res.errors:
        raise ParseError("No transaction rows found.")
    return res


class CSVParser(StatementParser):
    name, source_type = "generic_csv", "csv"

    def parse(self, content: bytes) -> ParseResult:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        if "\x00" in text:
            raise ParseError("File does not look like a CSV.")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            delim = dialect.delimiter
        except csv.Error:
            delim = ","
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim)]
        return _rows_to_result(rows)


class ExcelParser(StatementParser):
    name, source_type = "generic_excel", "excel"

    def parse(self, content: bytes) -> ParseResult:
        try:
            df = pd.read_excel(io.BytesIO(content), header=None, dtype=object, engine="openpyxl")
        except Exception:
            raise ParseError("Could not read Excel file. Only .xlsx/.xlsm files are supported.")
        rows = df.where(pd.notna(df), "").values.tolist()
        return _rows_to_result(rows)


# ---------- PDF ----------
_DATE_START = re.compile(r"^\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2}[ \-][A-Za-z]{3,9}[ \-,]+\d{2,4}|\d{4}-\d{2}-\d{2})\s+(.*)$")
_AMT_TOKEN = re.compile(r"(?<![\w/])(-?(?:₹|Rs\.?|INR)?\s?\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})|-?\d+\.\d{2})(?:\s?(Cr|Dr|CR|DR))?(?![\w/])")
_CR_WORDS = re.compile(r"(?i)salary|credit|deposit|refund|interest|cashback|reversal|received|\bCR\b")


class OCRProvider(ABC):
    @abstractmethod
    def extract_text(self, pdf_bytes: bytes) -> str: ...


class HttpOCRProvider(OCRProvider):
    """Generic OCR over HTTP: POST pdf bytes with bearer key; expects JSON {"text": "..."}.
    NOTE: provider-specific payloads vary - adapt here. Not exercised by the automated tests."""

    def extract_text(self, pdf_bytes: bytes) -> str:
        import httpx
        s = get_settings()
        r = httpx.post(s.ocr_api_url, content=pdf_bytes, timeout=60,
                       headers={"Authorization": f"Bearer {s.ocr_api_key}", "Content-Type": "application/pdf"})
        r.raise_for_status()
        return r.json().get("text", "")


def get_ocr_provider() -> OCRProvider | None:
    s = get_settings()
    if s.ocr_provider.lower() == "http" and s.ocr_api_url:
        return HttpOCRProvider()
    return None


class PDFParser(StatementParser):
    name, source_type = "generic_pdf", "pdf"

    def extract_text(self, content: bytes) -> str:
        import pymupdf
        try:
            doc = pymupdf.open(stream=content, filetype="pdf")
        except Exception:
            raise ParseError("Could not open PDF (corrupt or encrypted).")
        if doc.needs_pass:
            raise ParseError("PDF is password protected. Remove the password and try again.")
        text = "\n".join(page.get_text("text") for page in doc)
        if len(text.strip()) < 40:
            ocr = get_ocr_provider()
            if ocr is None:
                raise ParseError("This PDF has no extractable text (likely scanned). Configure an OCR provider "
                                 "(OCR_PROVIDER/OCR_API_URL) or upload a CSV/Excel export instead.")
            text = ocr.extract_text(content)
        return text

    def parse(self, content: bytes) -> ParseResult:
        return self.parse_text(self.extract_text(content))

    def parse_text(self, text: str) -> ParseResult:
        res = ParseResult()
        lines = [l.rstrip() for l in text.splitlines()]
        # pymupdf can emit each cell on its own line; rejoin lines that follow a date-only line
        merged: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if re.fullmatch(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", line) or re.fullmatch(r"\d{1,2}[ \-][A-Za-z]{3,9}[ \-,]+\d{2,4}", line):
                j, parts = i + 1, [line]
                while j < len(lines) and not re.fullmatch(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", lines[j].strip()) and j - i < 8:
                    parts.append(lines[j].strip())
                    j += 1
                merged.append(" ".join(p for p in parts if p))
                i = j
            else:
                merged.append(line)
                i += 1
        prev_balance: Decimal | None = None
        current: dict | None = None
        records: list[dict] = []
        for line in merged:
            m = _DATE_START.match(line)
            if m:
                rest = m.group(2)
                amts = [(a.group(1), a.group(2), a.start()) for a in _AMT_TOKEN.finditer(rest)]
                if amts:
                    current = {"date": m.group(1), "rest": rest, "amts": amts}
                    records.append(current)
                else:
                    current = {"date": m.group(1), "rest": rest, "amts": []}
                    records.append(current)
            elif current is not None and line and not _AMT_TOKEN.search(line):
                if not re.search(r"(?i)page \d|statement|opening balance|closing balance|total", line):
                    current["rest"] += " " + line
        for rec in records:
            res.total_rows += 1
            try:
                if not rec["amts"]:
                    raise ValueError("no amount found")
                d = parse_date(rec["date"])
                amts = rec["amts"]
                first_pos = amts[0][2]
                desc = rec["rest"][:first_pos].strip() or rec["rest"].strip()
                desc = re.sub(r"\s+", " ", desc)
                bal = None
                if len(amts) >= 2:
                    bal = parse_amount(amts[-1][0])[0]
                    amt_s, suf, _ = amts[-2]
                else:
                    amt_s, suf, _ = amts[0]
                amount = abs(parse_amount(amt_s)[0])
                low = False
                signed_neg = amt_s.strip().startswith("-")
                if suf:
                    direction = "debit" if suf.lower() == "dr" else "credit"
                elif signed_neg:
                    direction = "debit"
                elif bal is not None and prev_balance is not None:
                    delta = bal - prev_balance
                    if abs(abs(delta) - amount) <= Decimal("0.01"):
                        direction = "credit" if delta > 0 else "debit"
                    else:
                        direction, low = ("credit" if _CR_WORDS.search(desc) else "debit"), True
                else:
                    direction, low = ("credit" if _CR_WORDS.search(desc) else "debit"), True
                if amount == 0:
                    raise ValueError("zero amount")
                if bal is not None:
                    prev_balance = bal
                res.txns.append(RawTxn(d, desc, amount, direction, bal, low))
            except ValueError as e:
                res.errors.append({"row": rec["date"], "error": str(e)})
        if not res.txns:
            raise ParseError("No transactions could be recognised in this PDF. Try a CSV/Excel export, or add a bank-specific parser.")
        if any(t.low_confidence_direction for t in res.txns):
            res.warnings.append("Some PDF rows had ambiguous debit/credit direction and were inferred from keywords; please review.")
        return res


for _p in (CSVParser(), ExcelParser(), PDFParser()):
    register_parser(_p)
