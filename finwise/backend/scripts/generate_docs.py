"""Regenerate docs/DATABASE_SCHEMA.md and docs/openapi.json from the code."""
import json
from pathlib import Path

from app.core.database import Base
import app.models  # noqa: F401
from app.main import app

DOCS = Path(__file__).resolve().parent.parent / "docs"


def schema_md() -> str:
    out = ["# FinWise AI - Database Schema", "",
           "Generated from the SQLAlchemy models (`python -m scripts.generate_docs`). Source of truth for DDL: `alembic/versions/`.", "",
           "Conventions: UUID primary keys; every user-owned table has `user_id` -> `users.id` (ON DELETE CASCADE) and is always filtered by it;",
           "money columns are `NUMERIC(14,2)` (never float); timestamps are timezone-aware.", ""]
    for name, t in sorted(Base.metadata.tables.items()):
        out += [f"## `{name}`", "", "| Column | Type | Null | Key / Default |", "|---|---|---|---|"]
        for c in t.columns:
            keys = []
            if c.primary_key:
                keys.append("PK")
            keys += [f"FK -> {fk.target_fullname}" for fk in c.foreign_keys]
            if c.unique:
                keys.append("unique")
            if c.index:
                keys.append("indexed")
            out.append(f"| {c.name} | {c.type} | {'yes' if c.nullable else 'no'} | {', '.join(keys)} |")
        extra = [f"- {type(k).__name__}: ({', '.join(col.name for col in k.columns)})" for k in t.constraints if type(k).__name__ == "UniqueConstraint"]
        extra += [f"- Index `{i.name}`: ({', '.join(col.name for col in i.columns)})" for i in t.indexes]
        if extra:
            out += ["", "Constraints / indexes:", *extra]
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    (DOCS / "DATABASE_SCHEMA.md").write_text(schema_md())
    (DOCS / "openapi.json").write_text(json.dumps(app.openapi(), indent=2))
    print("docs written")
