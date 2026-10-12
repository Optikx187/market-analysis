import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "contracts" / "postgresql"
INVENTORY_PATH = CONTRACT_ROOT / "financial-fields.json"
SCHEMA_PATH = CONTRACT_ROOT / "schema.sql"
VERIFY_PATH = CONTRACT_ROOT / "verify.sql"
ARCHITECTURE_ROOT = ROOT / "docs" / "architecture" / "postgresql"


def durable_float_fields(model_path: Path) -> set[str]:
    tree = ast.parse(model_path.read_text(encoding="utf-8"))
    fields: set[str] = set()
    for class_node in tree.body:
        if not isinstance(class_node, ast.ClassDef):
            continue
        table_name = None
        for statement in class_node.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and statement.targets[0].id == "__tablename__"
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                table_name = statement.value.value
                break
        if table_name is None:
            continue
        for statement in class_node.body:
            if (
                not isinstance(statement, ast.Assign)
                or len(statement.targets) != 1
                or not isinstance(statement.targets[0], ast.Name)
                or not isinstance(statement.value, ast.Call)
                or not isinstance(statement.value.func, ast.Name)
                or statement.value.func.id != "Column"
                or not statement.value.args
                or not isinstance(statement.value.args[0], ast.Name)
                or statement.value.args[0].id != "Float"
            ):
                continue
            fields.add(f"{table_name}.{statement.targets[0].id}")
    return fields


def durable_table_names(model_path: Path) -> set[str]:
    tree = ast.parse(model_path.read_text(encoding="utf-8"))
    tables: set[str] = set()
    for class_node in tree.body:
        if not isinstance(class_node, ast.ClassDef):
            continue
        for statement in class_node.body:
            if (
                isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "__tablename__"
                    for target in statement.targets
                )
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                tables.add(statement.value.value)
    return tables


class FinancialFieldInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))

    def test_every_durable_float_has_one_decimal_mapping(self) -> None:
        model_fields: set[str] = set()
        for source_model in self.inventory["source_models"]:
            model_fields.update(durable_float_fields(ROOT / source_model))

        mapped_fields = [
            field
            for fields in self.inventory["legacy_float_fields"].values()
            for field in fields
        ]

        self.assertEqual(len(mapped_fields), len(set(mapped_fields)))
        self.assertEqual(model_fields, set(mapped_fields))

    def test_each_mapping_uses_a_declared_numeric_domain(self) -> None:
        domains = self.inventory["domains"]
        self.assertEqual(
            set(self.inventory["legacy_float_fields"]),
            {"price", "quantity", "money", "ratio", "score"},
        )
        for domain_name, domain in domains.items():
            with self.subTest(domain=domain_name):
                self.assertRegex(
                    domain["postgresql"],
                    r"^NUMERIC\(\d+,\d+\)$",
                )
                self.assertIn("ROUND_HALF_UP", domain["rounding"])

    def test_ratio_fields_define_percentage_point_conversion(self) -> None:
        self.assertEqual(
            self.inventory["domains"]["ratio"]["legacy_conversion"],
            "percentage_points_divide_by_100",
        )


class PostgreSQLPrototypeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        self.verify_sql = VERIFY_PATH.read_text(encoding="utf-8")

    def test_contract_uses_native_postgresql_types_without_binary_float(self) -> None:
        for logical_schema in (
            "identity",
            "market_data",
            "research",
            "portfolio",
            "notifications",
            "operations",
            "billing",
        ):
            self.assertIn(f"CREATE SCHEMA {logical_schema};", self.schema_sql)

        self.assertNotRegex(
            self.schema_sql,
            re.compile(r"\b(?:REAL|FLOAT|DOUBLE\s+PRECISION)\b", re.IGNORECASE),
        )
        self.assertIn("TIMESTAMPTZ", self.schema_sql)
        self.assertIn("UUID", self.schema_sql)
        self.assertIn("JSONB", self.schema_sql)

    def test_expected_numeric_declarations_are_prototyped(self) -> None:
        for declaration in (
            "NUMERIC(30,12)",
            "NUMERIC(38,18)",
            "NUMERIC(38,12)",
            "NUMERIC(24,12)",
            "NUMERIC(18,8)",
        ):
            self.assertIn(declaration, self.schema_sql)

    def test_query_plan_fixture_names_declared_indexes(self) -> None:
        for index_name in (
            "idx_candles_instrument_interval_time",
            "idx_paper_orders_open",
            "idx_delivery_jobs_due",
            "idx_audit_events_tenant_time",
        ):
            with self.subTest(index=index_name):
                self.assertIn(f"CREATE INDEX {index_name}", self.schema_sql)
        self.assertEqual(self.verify_sql.count(r"\echo PLAN "), 4)
        self.assertIn(r"\echo CONTRACT VERIFIED", self.verify_sql)

    def test_tenant_owned_roots_have_non_nullable_tenant_and_user_keys(self) -> None:
        for table in (
            "research.analysis_runs",
            "research.backtest_runs",
            "portfolio.portfolios",
            "portfolio.trades",
            "portfolio.paper_orders",
            "portfolio.live_orders",
            "notifications.destinations",
            "notifications.notifications",
        ):
            with self.subTest(table=table):
                match = re.search(
                    rf"CREATE TABLE {re.escape(table)} \((.*?)\n\);",
                    self.schema_sql,
                    re.DOTALL,
                )
                self.assertIsNotNone(match)
                table_body = match.group(1)
                self.assertIn("tenant_id UUID NOT NULL", table_body)
                self.assertIn("user_id UUID NOT NULL", table_body)

    def test_candle_natural_identity_includes_source_and_time(self) -> None:
        self.assertIn(
            "PRIMARY KEY (provider_id, instrument_id, bar_interval, bucket_start)",
            self.schema_sql,
        )


class ArchitectureDecisionTest(unittest.TestCase):
    def test_required_schema_boundaries_are_documented(self) -> None:
        schema_adr = (
            ARCHITECTURE_ROOT / "adr-0001-logical-schemas.md"
        ).read_text(encoding="utf-8")
        for logical_schema in (
            "`identity`",
            "`market_data`",
            "`research`",
            "`portfolio`",
            "`notifications`",
            "`operations`",
            "`billing`",
        ):
            self.assertIn(logical_schema, schema_adr)

    def test_identity_precision_and_partitioning_decisions_are_present(self) -> None:
        identifier_adr = (
            ARCHITECTURE_ROOT / "adr-0002-identifiers-tenancy-time.md"
        ).read_text(encoding="utf-8")
        precision_adr = (
            ARCHITECTURE_ROOT / "adr-0003-financial-precision.md"
        ).read_text(encoding="utf-8")
        indexing_adr = (
            ARCHITECTURE_ROOT / "adr-0004-indexing-retention-partitioning.md"
        ).read_text(encoding="utf-8")

        self.assertIn("UUIDv7", identifier_adr)
        self.assertIn("timestamp with time zone", identifier_adr)
        self.assertIn("ROUND_HALF_UP", precision_adr)
        self.assertIn("binary", precision_adr)
        self.assertIn("Aurora", indexing_adr)
        self.assertIn("RANGE", indexing_adr)

    def test_every_current_sqlalchemy_table_has_an_approved_destination(self) -> None:
        schema_adr = (
            ARCHITECTURE_ROOT / "adr-0001-logical-schemas.md"
        ).read_text(encoding="utf-8")
        current_tables: set[str] = set()
        for model_path in (
            ROOT / "services" / "data-ingestion" / "app" / "models.py",
            ROOT / "services" / "portfolio-engine" / "app" / "models.py",
        ):
            current_tables.update(durable_table_names(model_path))

        for table_name in current_tables:
            with self.subTest(table=table_name):
                self.assertIn(f"| `{table_name}` |", schema_adr)


if __name__ == "__main__":
    unittest.main()
