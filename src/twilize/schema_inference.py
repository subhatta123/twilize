"""Unified schema inference for multiple data sources.

Provides schema inference and column classification for:
  - CSV files (delegates to csv_to_hyper.infer_csv_schema)
  - Hyper extract files (reads schema via tableauhyperapi)
  - MySQL databases (queries INFORMATION_SCHEMA)
  - MSSQL databases (queries INFORMATION_SCHEMA)

All sources produce a ClassifiedSchema that feeds into the
chart suggestion and dashboard pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from twilize.csv_to_hyper import (
    ClassifiedSchema,
    ColumnSpec,
    CsvSchema,
    classify_columns,
    infer_csv_schema,
)

logger = logging.getLogger(__name__)

# ── Hyper schema inference ───────────────────────────────────────────

# Map Hyper type strings to our inferred types
_HYPER_TO_INFERRED = {
    "DOUBLE": "float",
    "FLOAT": "float",
    "BIG_INT": "integer",
    "INTEGER": "integer",
    "INT": "integer",
    "SMALL_INT": "integer",
    "NUMERIC": "float",
    "DATE": "date",
    "TIMESTAMP": "date",
    "TIMESTAMP_TZ": "date",
    "BOOL": "boolean",
    "TEXT": "string",
    "VARCHAR": "string",
    "CHAR": "string",
    "GEOGRAPHY": "string",
}


def infer_hyper_schema(
    hyper_path: str | Path,
    table_name: str = "",
) -> CsvSchema:
    """Infer schema from a Tableau Hyper extract file.

    Args:
        hyper_path: Path to the .hyper file.
        table_name: Specific table name to inspect (empty = first table).

    Returns:
        CsvSchema with column specifications inferred from Hyper metadata.
    """
    import shutil
    import tempfile

    from tableauhyperapi import (
        Connection,
        HyperProcess,
        Telemetry,
    )

    hyper_path = Path(hyper_path)
    if not hyper_path.exists():
        raise FileNotFoundError(f"Hyper file not found: {hyper_path}")

    # Copy to temp to handle locked files
    tmp_dir = tempfile.mkdtemp(prefix="twilize_hyper_schema_")
    tmp_path = Path(tmp_dir) / hyper_path.name

    try:
        shutil.copy2(hyper_path, tmp_path)

        hyper_log_dir = tempfile.mkdtemp(prefix="twilize_hyper_logs_")
        with HyperProcess(
            telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,
            parameters={"log_dir": hyper_log_dir},
        ) as hyper:
            with Connection(
                endpoint=hyper.endpoint,
                database=str(tmp_path),
            ) as conn:
                # Find target table
                target_table = None
                for schema_name in conn.catalog.get_schema_names():
                    for tbl in conn.catalog.get_table_names(schema_name):
                        if not table_name or tbl.name.unescaped == table_name:
                            target_table = tbl
                            break
                    if target_table:
                        break

                if target_table is None:
                    raise ValueError(
                        f"No table found in {hyper_path}"
                        + (f" matching '{table_name}'" if table_name else "")
                    )

                table_def = conn.catalog.get_table_definition(target_table)

                # Get row count
                result = conn.execute_scalar_query(
                    f"SELECT COUNT(*) FROM {target_table}"
                )
                row_count = int(result)

                # Build ColumnSpec for each column
                columns: list[ColumnSpec] = []
                for col in table_def.columns:
                    col_name = col.name.unescaped
                    hyper_type = str(col.type).upper()

                    # Normalize type string
                    for prefix in ("SQLTYPE.", "NULLABLE(", "("):
                        hyper_type = hyper_type.replace(prefix, "")
                    hyper_type = hyper_type.rstrip(")")

                    inferred = _HYPER_TO_INFERRED.get(hyper_type, "string")

                    # Sample values and cardinality via SQL
                    sample_values: list[str] = []
                    cardinality = 0
                    null_count = 0
                    try:
                        # Get cardinality
                        card_result = conn.execute_scalar_query(
                            f'SELECT COUNT(DISTINCT "{col_name}") FROM {target_table}'
                        )
                        cardinality = int(card_result)

                        # Get null count
                        null_result = conn.execute_scalar_query(
                            f'SELECT COUNT(*) FROM {target_table} WHERE "{col_name}" IS NULL'
                        )
                        null_count = int(null_result)

                        # Get sample values
                        rows = conn.execute_list_query(
                            f'SELECT DISTINCT "{col_name}" FROM {target_table} '
                            f'WHERE "{col_name}" IS NOT NULL LIMIT 5'
                        )
                        sample_values = [str(r[0]) for r in rows]
                    except Exception:
                        pass  # Schema-only fallback

                    columns.append(ColumnSpec(
                        name=col_name,
                        inferred_type=inferred,
                        sample_values=sample_values,
                        null_count=null_count,
                        cardinality=cardinality,
                        total_rows=row_count,
                    ))

        return CsvSchema(
            columns=columns,
            row_count=row_count,
            file_path=str(hyper_path),
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── SQL database schema inference ────────────────────────────────────

# Map SQL types to our inferred types
_SQL_TYPE_MAP = {
    # Integer types
    "int": "integer",
    "integer": "integer",
    "bigint": "integer",
    "smallint": "integer",
    "tinyint": "integer",
    "mediumint": "integer",
    "bit": "boolean",
    # Float types
    "float": "float",
    "double": "float",
    "decimal": "float",
    "numeric": "float",
    "real": "float",
    "money": "float",
    "smallmoney": "float",
    # Date types
    "date": "date",
    "datetime": "date",
    "datetime2": "date",
    "timestamp": "date",
    "smalldatetime": "date",
    "datetimeoffset": "date",
    "time": "string",
    "year": "integer",
    # Boolean
    "boolean": "boolean",
    "bool": "boolean",
    # String types
    "varchar": "string",
    "char": "string",
    "text": "string",
    "nvarchar": "string",
    "nchar": "string",
    "ntext": "string",
    "longtext": "string",
    "mediumtext": "string",
    "tinytext": "string",
    "enum": "string",
    "set": "string",
    "uniqueidentifier": "string",
    "xml": "string",
    "json": "string",
    # Binary (treat as string)
    "binary": "string",
    "varbinary": "string",
    "blob": "string",
    "image": "string",
}


def _map_sql_type(data_type: str) -> str:
    """Map a SQL data type name to our inferred type system."""
    return _SQL_TYPE_MAP.get(data_type.lower().split("(")[0].strip(), "string")


def infer_mysql_schema(
    server: str,
    dbname: str,
    table_name: str,
    username: str,
    password: str = "",
    port: int = 3306,
    sample_rows: int = 1000,
) -> CsvSchema:
    """Infer schema from a MySQL table.

    Queries INFORMATION_SCHEMA for column metadata and samples data
    for cardinality and sample values.

    Requires: pip install mysql-connector-python

    Args:
        server: MySQL server hostname.
        dbname: Database name.
        table_name: Table to inspect.
        username: Database username.
        password: Database password.
        port: Server port.
        sample_rows: Max rows to sample for statistics.

    Returns:
        CsvSchema with column specifications.
    """
    try:
        import mysql.connector
    except ImportError:
        raise ImportError(
            "mysql-connector-python is required for MySQL schema inference. "
            "Install with: pip install mysql-connector-python"
        )

    conn = mysql.connector.connect(
        host=server,
        database=dbname,
        user=username,
        password=password,
        port=port,
    )
    try:
        cursor = conn.cursor()

        # Get row count
        cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
        row_count = cursor.fetchone()[0]

        # Get column metadata from INFORMATION_SCHEMA
        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (dbname, table_name),
        )
        col_meta = cursor.fetchall()

        columns: list[ColumnSpec] = []
        for col_name, data_type in col_meta:
            inferred = _map_sql_type(data_type)

            # Get cardinality
            try:
                cursor.execute(
                    f"SELECT COUNT(DISTINCT `{col_name}`) FROM `{table_name}`"
                )
                cardinality = cursor.fetchone()[0]
            except Exception:
                cardinality = 0

            # Get null count
            try:
                cursor.execute(
                    f"SELECT COUNT(*) FROM `{table_name}` WHERE `{col_name}` IS NULL"
                )
                null_count = cursor.fetchone()[0]
            except Exception:
                null_count = 0

            # Get sample values
            sample_values: list[str] = []
            try:
                cursor.execute(
                    f"SELECT DISTINCT `{col_name}` FROM `{table_name}` "
                    f"WHERE `{col_name}` IS NOT NULL LIMIT 5"
                )
                sample_values = [str(r[0]) for r in cursor.fetchall()]
            except Exception:
                pass

            columns.append(ColumnSpec(
                name=col_name,
                inferred_type=inferred,
                sample_values=sample_values,
                null_count=null_count,
                cardinality=cardinality,
                total_rows=row_count,
            ))

        return CsvSchema(
            columns=columns,
            row_count=row_count,
            file_path=f"mysql://{server}/{dbname}/{table_name}",
        )
    finally:
        conn.close()


def infer_mssql_schema(
    server: str,
    dbname: str,
    table_name: str,
    username: str = "",
    password: str = "",
    port: int = 1433,
    trusted_connection: bool = False,
    sample_rows: int = 1000,
) -> CsvSchema:
    """Infer schema from a Microsoft SQL Server table.

    Queries INFORMATION_SCHEMA for column metadata and samples data
    for cardinality and sample values.

    Requires: pip install pyodbc

    Args:
        server: MSSQL server hostname.
        dbname: Database name.
        table_name: Table to inspect.
        username: Database username (ignored if trusted_connection).
        password: Database password (ignored if trusted_connection).
        port: Server port.
        trusted_connection: Use Windows Authentication.
        sample_rows: Max rows to sample for statistics.

    Returns:
        CsvSchema with column specifications.
    """
    try:
        import pyodbc
    except ImportError:
        raise ImportError(
            "pyodbc is required for MSSQL schema inference. "
            "Install with: pip install pyodbc"
        )

    # Build connection string
    if trusted_connection:
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server},{port};"
            f"DATABASE={dbname};"
            f"Trusted_Connection=yes;"
        )
    else:
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server},{port};"
            f"DATABASE={dbname};"
            f"UID={username};"
            f"PWD={password};"
        )

    conn = pyodbc.connect(conn_str)
    try:
        cursor = conn.cursor()

        # Get row count
        cursor.execute(f"SELECT COUNT(*) FROM [{table_name}]")
        row_count = cursor.fetchone()[0]

        # Get column metadata from INFORMATION_SCHEMA
        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = ? "
            "ORDER BY ORDINAL_POSITION",
            (table_name,),
        )
        col_meta = cursor.fetchall()

        columns: list[ColumnSpec] = []
        for col_name, data_type in col_meta:
            inferred = _map_sql_type(data_type)

            # Get cardinality
            try:
                cursor.execute(
                    f"SELECT COUNT(DISTINCT [{col_name}]) FROM [{table_name}]"
                )
                cardinality = cursor.fetchone()[0]
            except Exception:
                cardinality = 0

            # Get null count
            try:
                cursor.execute(
                    f"SELECT COUNT(*) FROM [{table_name}] WHERE [{col_name}] IS NULL"
                )
                null_count = cursor.fetchone()[0]
            except Exception:
                null_count = 0

            # Get sample values
            sample_values: list[str] = []
            try:
                cursor.execute(
                    f"SELECT DISTINCT TOP 5 [{col_name}] FROM [{table_name}] "
                    f"WHERE [{col_name}] IS NOT NULL"
                )
                sample_values = [str(r[0]) for r in cursor.fetchall()]
            except Exception:
                pass

            columns.append(ColumnSpec(
                name=col_name,
                inferred_type=inferred,
                sample_values=sample_values,
                null_count=null_count,
                cardinality=cardinality,
                total_rows=row_count,
            ))

        return CsvSchema(
            columns=columns,
            row_count=row_count,
            file_path=f"mssql://{server}/{dbname}/{table_name}",
        )
    finally:
        conn.close()


# ── Unified entry point ─────────────────────────────────────────────

def infer_schema(
    source: str,
    source_type: str = "auto",
    **kwargs,
) -> ClassifiedSchema:
    """Infer and classify schema from any supported data source.

    Args:
        source: Path to file (CSV, Hyper) or connection string.
        source_type: One of "csv", "hyper", "mysql", "mssql", "auto".
            "auto" detects from file extension or source prefix.
        **kwargs: Source-specific arguments (server, dbname, username, etc.)

    Returns:
        ClassifiedSchema with dimensions, measures, temporal, geographic.
    """
    if source_type == "auto":
        source_type = _detect_source_type(source)

    if source_type == "csv":
        raw = infer_csv_schema(
            source,
            sample_rows=kwargs.get("sample_rows", 1000),
            encoding=kwargs.get("encoding", "utf-8"),
        )
    elif source_type == "hyper":
        raw = infer_hyper_schema(
            source,
            table_name=kwargs.get("table_name", ""),
        )
    elif source_type == "mysql":
        raw = infer_mysql_schema(
            server=kwargs["server"],
            dbname=kwargs["dbname"],
            table_name=kwargs["table_name"],
            username=kwargs["username"],
            password=kwargs.get("password", ""),
            port=kwargs.get("port", 3306),
            sample_rows=kwargs.get("sample_rows", 1000),
        )
    elif source_type == "mssql":
        raw = infer_mssql_schema(
            server=kwargs["server"],
            dbname=kwargs["dbname"],
            table_name=kwargs["table_name"],
            username=kwargs.get("username", ""),
            password=kwargs.get("password", ""),
            port=kwargs.get("port", 1433),
            trusted_connection=kwargs.get("trusted_connection", False),
            sample_rows=kwargs.get("sample_rows", 1000),
        )
    else:
        raise ValueError(f"Unsupported source_type: {source_type}")

    return classify_columns(raw)


def _detect_source_type(source: str) -> str:
    """Auto-detect source type from path or URI."""
    lower = source.lower()
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith(".hyper"):
        return "hyper"
    if lower.startswith("mysql://"):
        return "mysql"
    if lower.startswith("mssql://"):
        return "mssql"
    # Default to CSV
    return "csv"
