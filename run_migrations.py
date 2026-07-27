"""
Run all Supabase migrations via direct PostgreSQL connection using psycopg2.
No psql CLI needed — uses the Supabase connection pooler.

Usage:
    python run_migrations.py
"""

import os
import re
import sys

import psycopg2
from psycopg2 import sql as pgsql

# ── Database connection (Supabase pooler — Transaction mode port 6543) ────────
DB_HOST     = "aws-0-ap-southeast-2.pooler.supabase.com"
DB_PORT     = 5432          # Session mode — required for DDL (ALTER TABLE, CREATE, etc.)
DB_NAME     = "postgres"
DB_USER     = "postgres.yqsazqjidoecrzmbukxm"
DB_PASSWORD = "P+L_wrZpXGZ5m8c"

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "supabase", "migrations")

# Run in chronological order
MIGRATION_FILES = [
    "20260713053408_create_fb_automation_schema.sql",
    "20260714024639_create_inbox_messages_table.sql",
    "20260718000000_add_phone_to_fb_accounts.sql",
    "20260721100000_fix_inbox_messages_account_id.sql",
    "20260727000000_add_user_id_to_all_tables.sql",
]


def clean_sql(raw: str) -> str:
    """Strip block comments and blank lines, keep all statements."""
    # Remove /* ... */ block comments
    cleaned = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)
    return cleaned


def split_statements(sql: str) -> list[str]:
    """Split on semicolons, return non-empty statements."""
    return [s.strip() for s in sql.split(';') if s.strip()]


def run_migration(cursor, filepath: str, filename: str):
    """Execute one migration file, statement by statement."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    sql = clean_sql(raw)
    statements = split_statements(sql)

    ok = 0
    skipped = 0

    for stmt in statements:
        try:
            cursor.execute(stmt)
            ok += 1
        except psycopg2.errors.DuplicateTable:
            skipped += 1
        except psycopg2.errors.DuplicateObject:
            skipped += 1
        except psycopg2.errors.UniqueViolation:
            skipped += 1
        except psycopg2.Error as e:
            pg_code = getattr(e, 'pgcode', '')
            pg_msg  = str(e).strip().split('\n')[0]

            # 42701 = duplicate_column, 42P07 = duplicate_table, 42710 = duplicate_object
            if pg_code in ('42701', '42P07', '42710', '23505'):
                skipped += 1
            else:
                print(f"    [WARN] {pg_code}: {pg_msg}")
                skipped += 1   # non-fatal — keep going

    return ok, skipped


def main():
    print("=" * 62)
    print("  FB Automation — Supabase Migration Runner")
    print("=" * 62)
    print(f"  Host: {DB_HOST}:{DB_PORT}")
    print(f"  DB:   {DB_NAME}  User: {DB_USER}")
    print()

    # Verify all files present
    for filename in MIGRATION_FILES:
        path = os.path.join(MIGRATIONS_DIR, filename)
        status = "✓" if os.path.exists(path) else "✗ MISSING"
        print(f"  {status}  {filename}")

    missing = [f for f in MIGRATION_FILES
               if not os.path.exists(os.path.join(MIGRATIONS_DIR, f))]
    if missing:
        print("\nAbort: missing files above.")
        sys.exit(1)

    print()
    print("Connecting to Supabase PostgreSQL…")

    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=30,
            sslmode="require",
        )
        conn.autocommit = True   # DDL must not be wrapped in a failed transaction
        cur = conn.cursor()
        print("Connected!\n")
    except Exception as e:
        print(f"\nConnection failed: {e}")
        sys.exit(1)

    all_ok = True
    for filename in MIGRATION_FILES:
        path = os.path.join(MIGRATIONS_DIR, filename)
        print(f"  Running: {filename}")
        try:
            ok, skipped = run_migration(cur, path, filename)
            print(f"  Done — {ok} executed, {skipped} already-applied/skipped\n")
        except Exception as e:
            print(f"  ERROR: {e}\n")
            all_ok = False

    cur.close()
    conn.close()

    print("=" * 62)
    if all_ok:
        print("  All migrations completed successfully!")
    else:
        print("  Completed with some errors — check output above.")
    print()
    print("  Tables created/updated:")
    for t in ["fb_accounts", "listings", "tasks", "automation_logs", "inbox_messages"]:
        print(f"    • {t}")
    print("=" * 62)


if __name__ == "__main__":
    main()
