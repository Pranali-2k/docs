"""The Python functions for the Docs Health insert procedures, and the schema they need.

No database: a recording connection stands in for PostgreSQL. What is checked here is that
the functions match the procedures parameter for parameter, that the schema holds every table
and column the procedures insert into, and that the sample-data script only makes calls the
functions accept. The procedures themselves (plpgsql) run only on a real PostgreSQL.
"""
from __future__ import annotations

import inspect
import re
from datetime import date
from pathlib import Path

import pytest
from psycopg import errors
from psycopg.types.json import Jsonb

from scripts.procs import docs_procedures as db
from scripts.procs import generate, seed_sample_data

PROCS = Path(db.__file__).with_name("procedures.sql")
SCHEMA = Path(db.__file__).with_name("schema.sql")


def procedures() -> list[dict]:
    return generate.parse_procedures(PROCS.read_text(encoding="utf-8"))


def test_the_file_holds_25_procedures_and_each_has_a_function() -> None:
    found = procedures()
    assert len(found) == 25 and set(db.PROCEDURES) == {p["name"] for p in found}
    for proc in found:
        function = getattr(db, proc["name"])
        parameters = [n for n in inspect.signature(function).parameters if n not in ("conn", "commit")]
        assert parameters == [p[2:] for p, _t in proc["params"]], proc["name"]
        assert db.PROCEDURES[proc["name"]] == (proc["table"], proc["params"])


def test_generated_files_are_up_to_date_with_the_procedure_file() -> None:
    """Edit procedures.sql, then run `python -m scripts.procs.generate`: this fails until you do."""
    assert Path(db.__file__).read_text(encoding="utf-8") == generate.module_source(procedures())


def test_the_call_binds_values_and_casts_to_the_procedures_own_types() -> None:
    sql = db.call_sql("insert_payer")
    assert sql == ("CALL public.insert_payer(%(p_name)s::VARCHAR(200), %(p_payer_code)s::VARCHAR(40), "
                   "%(p_aliases)s::JSONB, %(p_electronic_payer_id)s::VARCHAR(20), "
                   "%(p_financial_category)s::VARCHAR(40), %(p_claims_addresses)s::JSONB)")


class Cursor:
    def __init__(self, conn): self.conn = conn
    def __enter__(self): return self
    def __exit__(self, *exc): return False

    def execute(self, sql, values=None):
        if self.conn.raises:
            raise errors.RaiseException(self.conn.raises)
        self.conn.calls.append((sql, values))


class Conn:
    def __init__(self, raises=None):
        self.calls, self.raises, self.commits, self.rollbacks = [], raises, 0, 0

    def cursor(self): return Cursor(self)
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


def test_a_function_sends_its_arguments_and_commits() -> None:
    conn = Conn()
    db.insert_payer(conn, name="Aetna", payer_code="AETNA", aliases=["Aetna Inc."])
    sql, values = conn.calls[0]
    assert sql.startswith("CALL public.insert_payer(") and values["p_name"] == "Aetna"
    assert isinstance(values["p_aliases"], Jsonb) and values["p_claims_addresses"] is None  # JSON wrapped, absent is NULL
    assert conn.commits == 1
    db.insert_payer_plan(conn, payer_id=1, name="PPO", plan_type="PPO", commit=False)
    assert conn.commits == 1  # the caller can keep one transaction


def test_the_procedures_message_becomes_the_error_and_the_transaction_is_rolled_back() -> None:
    conn = Conn(raises="Payer name already exists: Aetna")
    with pytest.raises(db.ProcedureError, match="Payer name already exists: Aetna"):
        db.insert_payer(conn, name="Aetna", payer_code="X", aliases=[])
    assert conn.rollbacks == 1 and conn.commits == 0


# ------------------------------------------------------------------------------------ schema
def tables_in_schema() -> dict[str, dict[str, str]]:
    """{table: {column: definition}} from schema.sql."""
    out = {}
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA.read_text("utf-8"), re.S):
        columns = {}
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            match = re.match(r'^("?[a-z_0-9]+"?)\s+(.*)$', line)
            if match and not re.match(r"(CONSTRAINT|PRIMARY|UNIQUE|FOREIGN|CHECK)\b", line):
                columns[match.group(1).strip('"')] = match.group(2)
        out[name] = columns
    return out


def test_the_schema_has_every_table_and_column_the_procedures_insert_into() -> None:
    schema = tables_in_schema()
    assert len(schema) == 27
    text = PROCS.read_text(encoding="utf-8")
    inserts = re.findall(r"INSERT INTO public\.(\w+)\s*\((.*?)\)\s*VALUES", text, re.S)
    assert len(inserts) == 25
    for table, columns in inserts:
        columns = [c.strip().strip('"') for c in columns.split(",")]
        assert table in schema, f"{table} is not in schema.sql"
        assert not [c for c in columns if c not in schema[table]], (table, [c for c in columns if c not in schema[table]])


def test_a_procedure_can_insert_without_columns_it_does_not_know_about() -> None:
    """A NOT NULL column with no default that a procedure does not set would make every call fail."""
    schema = tables_in_schema()
    text = PROCS.read_text(encoding="utf-8")
    problems = []
    for table, columns in re.findall(r"INSERT INTO public\.(\w+)\s*\((.*?)\)\s*VALUES", text, re.S):
        given = {c.strip().strip('"') for c in columns.split(",")}
        for column, definition in schema[table].items():
            required = "NOT NULL" in definition and "DEFAULT" not in definition and "SERIAL" not in definition.upper()
            primary = "PRIMARY KEY" in definition.upper()
            if required and column not in given:
                problems.append(f"{table}.{column}")
            if primary and "SERIAL" not in definition.upper() and column not in given and "PRIMARY KEY" in definition:
                problems.append(f"{table}.{column} (primary key)")
    assert not problems, problems


def test_the_other_appointments_table_is_not_created_twice() -> None:
    text = SCHEMA.read_text(encoding="utf-8")
    assert len(re.findall(r"CREATE TABLE IF NOT EXISTS appointments \(", text)) == 1
    assert "patient_email TEXT" in text and "id SERIAL NOT NULL UNIQUE" in text


# ------------------------------------------------------------------------------- sample data
class SeedConn(Conn):
    """Answers the seed's lookups with an id and its 'is there already?' checks with 'no'."""

    def execute(self, query, params=None):
        self.calls.append((query, params))
        row = None if query.startswith("SELECT 1") else (1,)
        return type("Result", (), {"fetchone": lambda self_: row})()


def test_the_sample_data_script_only_makes_calls_the_functions_accept(monkeypatch) -> None:
    calls: list[str] = []
    for name in db.PROCEDURES:
        original = getattr(db, name)

        def spy(conn, *, _name=name, _original=original, **kwargs):
            _original(conn, **kwargs)  # TypeError here means a wrong argument name
            calls.append(_name)

        monkeypatch.setattr(db, name, spy)
    conn = SeedConn()
    tally = seed_sample_data.seed(conn)
    assert tally.added == len(calls) and tally.added > 60
    assert set(calls) == set(db.PROCEDURES), set(db.PROCEDURES) - set(calls)  # every procedure is exercised
    # Parents before children: a patient exists before its policy, a payer before its plan.
    assert calls.index("insert_patient") < calls.index("insert_insurance_policy")
    assert calls.index("insert_payer") < calls.index("insert_payer_plan")
    assert calls.index("insert_email_message") < calls.index("insert_portal_request")


def test_the_sample_patients_are_modmed_style_people_with_real_looking_fields() -> None:
    people = seed_sample_data.load_patients(8)
    assert 1 <= len(people) <= 8
    for p in people:
        assert p["id"].isdigit() and p["family"] and date.fromisoformat(p["dob"])
