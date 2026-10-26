from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TableReport:
    source_rows: int = 0
    imported_rows: int = 0
    skipped_rows: int = 0
    deferred_rows: int = 0
    conflict_rows: int = 0
    source_checksum: str = ""
    target_checksum: str = ""


@dataclass
class ImportReport:
    mode: str
    source_fingerprint_sha256: str
    import_run_id: str | None = None
    tables: dict[str, TableReport] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    financial_reconciliation: dict[str, object] = field(default_factory=dict)
    source_unchanged: bool = False
    reconciled: bool = False

    @property
    def blocked(self) -> bool:
        return bool(self.errors or self.decisions)

    def table(self, store: str, table: str) -> TableReport:
        return self.tables.setdefault(f"{store}.{table}", TableReport())

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "import_run_id": self.import_run_id,
            "source_fingerprint_sha256": self.source_fingerprint_sha256,
            "blocked": self.blocked,
            "reconciled": self.reconciled,
            "source_unchanged": self.source_unchanged,
            "errors": self.errors,
            "warnings": self.warnings,
            "operator_decisions": self.decisions,
            "tables": {
                name: vars(value)
                for name, value in sorted(self.tables.items())
            },
            "financial_reconciliation": self.financial_reconciliation,
        }
