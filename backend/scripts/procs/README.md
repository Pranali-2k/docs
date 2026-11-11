# Docs Health insert procedures: tables, Python functions, sample data

Built from `procedures.sql` (the 25 `insert_*` procedures).

| File | What it is |
|---|---|
| `procedures.sql` | Your procedure file, unchanged. |
| `schema.sql` | The 27 tables the procedures write to. 22 come from `app/models.py`; 5 are Dr Patel's (`appointments`, `summaries`, `session_registry`, `session_summaries`, `emr_exports`). |
| `docs_procedures.py` | One Python function per procedure (`insert_user`, `insert_patient`, ...). Generated. |
| `seed_sample_data.py` | Sample rows, loaded through those functions. ModMed sandbox patients. |
| `bootstrap_procs_db.py` | Creates a separate database and loads all of the above. Run it yourself. |
| `generate.py` | Regenerates `schema.sql` and `docs_procedures.py` after `procedures.sql` or the models change. |

## Create the database

    cd "C:\DOCS Health PRD\backend"
    .venv\Scripts\python.exe -m scripts.procs.bootstrap_procs_db

It asks for the PostgreSQL superuser password, creates `docs_health_procs` (use `--db` to rename),
applies the schema and procedures, and loads the sample data. Safe to repeat. Existing databases are untouched.

## Use the functions

    import psycopg
    from scripts.procs import docs_procedures as db

    with psycopg.connect("postgresql://user:password@localhost:5432/docs_health_procs") as conn:
        db.insert_payer(conn, name="Aetna", payer_code="AETNA", aliases=["Aetna Inc."])
        try:
            db.insert_payer(conn, name="Aetna", payer_code="AETNA2", aliases=[])
        except db.ProcedureError as error:
            print(error)        # Payer name already exists: Aetna

Arguments are the procedure's parameters without `p_`. Every one is optional in Python; the procedure
decides which are required and raises `ProcedureError` with its own message. Pass `commit=False` to
run several calls in one transaction. JSONB arguments take dicts and lists.

## The `appointments` table

The procedure `insert_appointment` writes Dr Patel's `appointments` shape (`appointment_id`, `clinician`,
`scheduled_for`, ...). Docs Health's own table of the same name has different columns, and a database can
hold only one, so this database uses the Dr Patel shape (plus an `id` column so `portal_requests.appointment_id`
can still point at it). Do not load this schema into the Docs Health application database.

## Tests

    .venv\Scripts\python.exe -m pytest tests/test_procs_functions.py

These run without a database. They check that each function matches its procedure, that the schema has every
column the procedures insert, and that the sample-data script only makes valid calls. The procedures themselves
run only on PostgreSQL, so the first real run is `bootstrap_procs_db`.
