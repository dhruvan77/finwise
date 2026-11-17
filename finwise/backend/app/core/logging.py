import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[a-z0-9\-_\.]+"), "Bearer ***"),
    (re.compile(r"\b\d{9,18}\b"), "[num]"),            # account / card-like numbers
    (re.compile(r"(?i)(api[_-]?key|password|secret)=\S+"), r"\1=***"),
]


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for rx, rep in _PATTERNS:
            msg = rx.sub(rep, msg)
        record.msg, record.args = msg, ()
        return True


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in logging.getLogger().handlers:
        h.addFilter(RedactFilter())
