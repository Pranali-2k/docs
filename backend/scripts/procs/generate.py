"""Generate the schema and the Python functions from the stored-procedure file.

    python -m scripts.procs.generate

Reads  scripts/procs/procedures.sql   (the 25 insert_* procedures)
Writes scripts/procs/schema.sql       (every table the procedures need, PostgreSQL DDL)
       scripts/procs/docs_procedures.py   (one Python function per procedure)

The 22 Docs Health tables come from app/models.py, so they are exactly the ones the
application uses. The procedures also write Dr Patel's tables (summaries,
session_summaries, session_registry, emr_exports) and a Dr Patel-shaped `appointments`
table; those are defined here, from the procedures' own columns and Dr Patel's DDL.

Docs Health's own `appointments` table is NOT created: the procedure's `appointments`
has different columns, and a database can hold only one table of that name. Docs Health
tables that point at an appointment (portal_requests.appointment_id) point at the
`id` column this script adds to the Dr Patel-shaped table.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app import models  # noqa: F401
from app.db import Base

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "procedures.sql"

#: The Dr Patel-side tables, as the procedures use them. Types come from the procedure
#: parameters and Dr Patel's own CREATE TABLE statements (database.py, appointments.py).
EXTRA_TABLES = """\
-- Dr Patel-shaped tables used by the procedures ------------------------------------------

CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL NOT NULL UNIQUE,              -- lets Docs Health tables reference an appointment by number
    appointment_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64),
    patient_id VARCHAR(128),
    icd_10 TEXT,
    cpt TEXT,
    patient_name TEXT,
    scheduled_for TIMESTAMPTZ,
    duration_minutes INTEGER,
    clinician TEXT,
    reason TEXT,
    status TEXT,
    booked_via TEXT,
    patient_email TEXT,
    email_status TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Two patients cannot hold the same slot with the same clinician.
CREATE UNIQUE INDEX IF NOT EXISTS appointments_slot_idx ON appointments (scheduled_for, clinician) WHERE status = 'booked';
CREATE INDEX IF NOT EXISTS appointments_patient_idx ON appointments (patient_id);

CREATE TABLE IF NOT EXISTS summaries (
    summary_id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    patient_id BIGINT NOT NULL,
    chief_complaint TEXT,
    current_symptoms TEXT,
    allergies TEXT,
    past_history TEXT,
    additional_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS summaries_session_idx ON summaries (session_id);
CREATE INDEX IF NOT EXISTS summaries_patient_idx ON summaries (patient_id);

CREATE TABLE IF NOT EXISTS session_registry (
    session_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(128),
    patient_name TEXT,
    "timestamp" TIMESTAMPTZ,
    conversation_table TEXT
);

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(128),
    patient_name TEXT,
    json_filename TEXT,
    language TEXT,
    message_count INTEGER DEFAULT 0,
    generated_at TIMESTAMPTZ,
    chief_complaint TEXT,
    history_of_present_illness TEXT,
    current_symptoms TEXT,
    allergies TEXT,
    current_medications TEXT,
    past_medical_history TEXT,
    family_history TEXT,
    social_history TEXT,
    other_notes TEXT,
    summary_text TEXT,
    llm_provider TEXT,
    llm_model TEXT,
    icd_10 TEXT,
    icd_10_details JSONB,
    icd_10_status TEXT,
    cpt TEXT,
    cpt_details JSONB,
    cpt_status TEXT,
    exam_findings TEXT,
    assessment TEXT,
    plan_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS emr_exports (
    export_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64),
    patient_id VARCHAR(128),
    patient_name TEXT,
    exported_at TIMESTAMPTZ,
    export_file TEXT,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_PY_TYPES = {
    "VARCHAR": "str", "TEXT": "str", "INTEGER": "int", "BIGINT": "int", "BOOLEAN": "bool",
    "DATE": "date", "TIME": "time", "TIMESTAMPTZ": "datetime", "UUID": "UUID | str",
    "JSONB": "dict | list", "NUMERIC": "Decimal | float", "DOUBLE PRECISION": "float",
}


def parse_procedures(text: str) -> list[dict]:
    """[{name, params: [(p_name, sql_type)], table}] in file order."""
    out = []
    for match in re.finditer(r"CREATE OR REPLACE PROCEDURE public\.(\w+)\((.*?)\)\s*LANGUAGE", text, re.S):
        params = []
        for line in match.group(2).splitlines():
            m = re.match(r"^\s*IN\s+(p_\w+)\s+(.+?),?\s*$", line)
            if m:
                params.append((m.group(1), m.group(2).strip()))
        out.append({"name": match.group(1), "params": params})
    inserts = re.findall(r"INSERT INTO public\.(\w+)\s*\(", text)
    for proc, table in zip(out, inserts):
        proc["table"] = table
    return out


def docs_health_ddl() -> str:
    dialect = postgresql.dialect()
    parts = ["-- Docs Health tables (generated from app/models.py by scripts/procs/generate.py) ----", ""]
    deferred = []
    for table in Base.metadata.sorted_tables:
        if table.name == "appointments":
            continue  # the Dr Patel-shaped table in EXTRA_TABLES takes this name
        ddl = str(CreateTable(table).compile(dialect=dialect)).strip()
        parts.append(ddl.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1) + ";\n")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            parts.append(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect)).strip() + ";")
        parts.append("")
        deferred += [fk for fk in table.foreign_key_constraints if fk.use_alter]
    if deferred:
        parts.append("-- Circular references, added once every table exists")
        for fk in deferred:
            cols = ", ".join(c.name for c in fk.columns)
            ref = fk.elements[0].column.table.name
            refcols = ", ".join(e.column.name for e in fk.elements)
            parts.append(f"ALTER TABLE {fk.table.name} DROP CONSTRAINT IF EXISTS {fk.name};")  # repeatable
            parts.append(f"ALTER TABLE {fk.table.name} ADD CONSTRAINT {fk.name} FOREIGN KEY ({cols}) "
                         f"REFERENCES {ref} ({refcols}) ON DELETE {fk.ondelete or 'NO ACTION'};")
    return "\n".join(parts)


def py_type(sql_type: str) -> str:
    base = re.sub(r"\(.*\)", "", sql_type).strip().upper()
    return _PY_TYPES.get(base, "str")


def cast_type(sql_type: str) -> str:
    return sql_type.strip()


def function_source(proc: dict) -> str:
    name, params = proc["name"], proc["params"]
    args = "".join(f"    {p[2:]}: {py_type(t)} | None = None,\n" for p, t in params)
    def value(p: str, t: str) -> str:
        arg = p[2:]
        return f"Jsonb({arg}) if {arg} is not None else None" if t.upper().startswith("JSONB") else arg

    values = "".join(f'        "{p}": {value(p, t)},\n' for p, t in params)
    doc_params = "\n".join(f"        {p[2:]}: {t}" for p, t in params)
    return f'''

def {name}(
    conn: Connection,
    *,
{args}    commit: bool = True,
) -> None:
    """CALL public.{name}(...): insert one row into `{proc["table"]}`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

{doc_params}
    """
    _call(
        conn,
        "{name}",
        {{
{values}        }},
        commit=commit,
    )
'''


def module_source(procs: list[dict]) -> str:
    table = "{\n" + "".join(
        f'    "{p["name"]}": ({p["table"]!r}, [' + ", ".join(f"({a!r}, {t!r})" for a, t in p["params"]) + "]),\n"
        for p in procs) + "}"
    body = "".join(function_source(p) for p in procs)
    return f'''"""Python functions for the Docs Health insert procedures (generated; do not edit by hand).

Regenerate with:  python -m scripts.procs.generate

One function per procedure in scripts/procs/procedures.sql. Each takes an open psycopg
connection and the procedure's arguments (without the p_ prefix), calls the procedure,
and turns the procedure's RAISE EXCEPTION into a ProcedureError carrying its message:

    import psycopg
    from scripts.procs import docs_procedures as db

    with psycopg.connect(url) as conn:
        db.insert_payer(conn, name="Aetna", payer_code="AETNA", aliases=["Aetna Inc."])
        try:
            db.insert_payer(conn, name="Aetna", payer_code="AETNA2", aliases=[])
        except db.ProcedureError as error:
            print(error)          # Payer name already exists: Aetna

Parameters are bound by the driver and cast to the procedure's own parameter types, so
nothing is formatted into SQL text. JSONB arguments take Python dicts and lists.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg import Connection, errors
from psycopg.types.json import Jsonb

#: procedure -> (table it writes, [(parameter, SQL type), ...])
PROCEDURES: dict[str, tuple[str, list[tuple[str, str]]]] = {table}


class ProcedureError(Exception):
    """The procedure rejected the call (its RAISE EXCEPTION message is the text)."""


def call_sql(name: str) -> str:
    """The CALL statement for a procedure, with named placeholders and explicit casts."""
    _table, params = PROCEDURES[name]
    placeholders = ", ".join(f"%({{p}})s::{{t}}" for p, t in params)
    return f"CALL public.{{name}}({{placeholders}})"


def _call(conn: Connection, name: str, values: dict, *, commit: bool) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(call_sql(name), values)
    except errors.RaiseException as error:
        conn.rollback()
        raise ProcedureError(error.diag.message_primary or str(error)) from None
    except psycopg.Error:
        conn.rollback()
        raise
    if commit:
        conn.commit()
{body}'''


def main() -> None:
    procs = parse_procedures(SOURCE.read_text(encoding="utf-8"))
    (HERE / "schema.sql").write_text(
        "-- Schema for the Docs Health insert procedures. Safe to run on an empty database.\n"
        "-- Generated by scripts/procs/generate.py - do not edit by hand.\n\n"
        + EXTRA_TABLES + "\n" + docs_health_ddl() + "\n",
        encoding="utf-8",
    )
    (HERE / "docs_procedures.py").write_text(module_source(procs), encoding="utf-8")
    print(f"{len(procs)} procedures -> docs_procedures.py; schema.sql written")


if __name__ == "__main__":
    main()
