import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config

from database.config import get_database_settings, get_database_url
from database.models import ApiKey, AuditLog, Base, Plan, Project, UsageRecord, User


class DatabaseConfigTests(unittest.TestCase):
    def test_database_url_must_be_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                get_database_url()

    def test_config_reads_pool_and_timeout_settings(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+psycopg://postgres:secret@localhost:5432/cognisense",
                "DATABASE_ECHO": "true",
                "DATABASE_POOL_SIZE": "12",
                "DATABASE_MAX_OVERFLOW": "8",
                "DATABASE_POOL_TIMEOUT": "45",
                "DATABASE_CONNECT_TIMEOUT": "15",
            },
            clear=True,
        ):
            settings = get_database_settings()
            self.assertEqual(settings.database_url, "postgresql+psycopg://postgres:secret@localhost:5432/cognisense")
            self.assertTrue(settings.echo_sql)
            self.assertEqual(settings.pool_size, 12)
            self.assertEqual(settings.max_overflow, 8)
            self.assertEqual(settings.pool_timeout, 45)
            self.assertEqual(settings.connect_timeout, 15)


class DatabaseModelTests(unittest.TestCase):
    def test_metadata_contains_expected_tables_and_relationships(self):
        tables = {table.name for table in Base.metadata.sorted_tables}
        self.assertTrue({"users", "projects", "api_keys", "usage_records", "plans", "audit_logs"}.issubset(tables))

        users_table = Base.metadata.tables["users"]
        audit_logs_table = Base.metadata.tables["audit_logs"]
        projects_table = Base.metadata.tables["projects"]
        api_keys_table = Base.metadata.tables["api_keys"]
        usage_records_table = Base.metadata.tables["usage_records"]

        self.assertIn("email", users_table.columns)
        self.assertEqual(users_table.columns["role"].default.arg, "user")
        self.assertIn("actor_identifier", audit_logs_table.columns)
        self.assertIn("action", audit_logs_table.columns)
        self.assertIn("target_identifier", audit_logs_table.columns)
        self.assertIn("occurred_at", audit_logs_table.columns)
        self.assertIn("outcome", audit_logs_table.columns)
        self.assertTrue(users_table.columns["email"].unique)
        self.assertIn("owner_user_id", projects_table.columns)
        self.assertIn("public_project_id", projects_table.columns)
        self.assertIn("plan_id", projects_table.columns)
        self.assertIn("project_id", api_keys_table.columns)
        self.assertIn("api_key_id", usage_records_table.columns)

        self.assertTrue(any(fk.column.table.name == "users" for fk in projects_table.foreign_keys))
        self.assertTrue(any(fk.column.table.name == "projects" for fk in api_keys_table.foreign_keys))
        self.assertTrue(any(fk.column.table.name == "api_keys" for fk in usage_records_table.foreign_keys))

        self.assertEqual(User.__tablename__, "users")
        self.assertEqual(Project.__tablename__, "projects")
        self.assertEqual(ApiKey.__tablename__, "api_keys")
        self.assertEqual(UsageRecord.__tablename__, "usage_records")
        self.assertEqual(Plan.__tablename__, "plans")
        self.assertEqual(AuditLog.__tablename__, "audit_logs")


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "A dedicated PostgreSQL TEST_DATABASE_URL is required to exercise Alembic migrations.")
class DatabaseMigrationTests(unittest.TestCase):
    def test_alembic_upgrade_head_creates_schema(self):
        url = os.environ["TEST_DATABASE_URL"]
        database_name = make_url(url).database or ""
        if "test" not in database_name.lower():
            self.skipTest("TEST_DATABASE_URL must target a dedicated database whose name includes 'test'.")
        try:
            engine = create_engine(url, future=True)
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:  # pragma: no cover - environment-specific skip.
            self.skipTest("PostgreSQL test database is unavailable.")

        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)

        try:
            with patch.dict(os.environ, {"DATABASE_URL": url}):
                command.upgrade(config, "head")
            inspector = inspect(engine)
            self.assertTrue({"users", "projects", "api_keys", "usage_records", "plans", "audit_logs"}.issubset(inspector.get_table_names()))
            self.assertIn("role", {column["name"] for column in inspector.get_columns("users")})
        finally:
            engine.dispose()
