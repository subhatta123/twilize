"""Connection configuration mixin for TWBEditor.

Handles MySQL and Tableau Server connection setup.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import List, Optional

from lxml import etree

from .config import _generate_uuid


def inspect_hyper_schema(filepath: str) -> dict:
    """Read a Hyper file and return its schema.

    Returns a dict of the form::

        {"tables": [
            {"schema": "Extract", "name": "Orders",
             "columns": [{"name": "Sales", "type": "double"}, ...]},
            ...
        ]}

    If the file is locked (e.g. open in Tableau), it is copied to a
    temporary location first.
    """
    from tableauhyperapi import HyperProcess, Connection, Telemetry, SchemaName

    # Copy to a temp file so we don't fail on locked hyper files
    tmp_dir = tempfile.mkdtemp(prefix="twilize_hyper_")
    tmp_path = os.path.join(tmp_dir, os.path.basename(filepath))
    try:
        shutil.copy2(filepath, tmp_path)

        tables_out: list[dict] = []
        with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
            with Connection(
                endpoint=hyper.endpoint,
                database=tmp_path,
            ) as conn:
                for schema_name in conn.catalog.get_schema_names():
                    for table_name in conn.catalog.get_table_names(schema_name):
                        table_def = conn.catalog.get_table_definition(table_name)
                        columns = []
                        for col in table_def.columns:
                            columns.append({
                                "name": col.name.unescaped,
                                "type": str(col.type),
                            })
                        tables_out.append({
                            "schema": str(schema_name),
                            "name": table_name.name.unescaped,
                            "columns": columns,
                        })
        return {"tables": tables_out}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class ConnectionsMixin:
    """Mixin providing database connection methods for TWBEditor."""

    def set_mysql_connection(
        self,
        server: str,
        dbname: str,
        username: str,
        table_name: str,
        port: str = "3306",
    ) -> str:
        """Configure the datasource to use a Local MySQL connection."""
        # 1. Update <connection class='federated'>
        fed_conn = self._datasource.find("connection[@class='federated']")
        if fed_conn is None:
            for old_conn in self._datasource.findall("connection"):
                self._datasource.remove(old_conn)
            fed_conn = etree.Element("connection")
            fed_conn.set("class", "federated")
            self._datasource.insert(0, fed_conn)

        # Update <named-connections>
        named_conns = fed_conn.find("named-connections")
        if named_conns is None:
            named_conns = etree.SubElement(fed_conn, "named-connections")
        else:
            for child in list(named_conns):
                named_conns.remove(child)

        conn_name = f"mysql.{_generate_uuid().strip('{}').lower()}"

        nc = etree.SubElement(named_conns, "named-connection")
        nc.set("caption", server)
        nc.set("name", conn_name)

        mysql_conn = etree.SubElement(nc, "connection")
        mysql_conn.set("class", "mysql")
        mysql_conn.set("dbname", dbname)
        mysql_conn.set("odbc-native-protocol", "")
        mysql_conn.set("one-time-sql", "")
        mysql_conn.set("port", str(port))
        mysql_conn.set("server", server)
        mysql_conn.set("source-charset", "")
        mysql_conn.set("username", username)

        # 2. Update <relation>
        relation = fed_conn.find("relation")
        if relation is None:
            relation = etree.SubElement(fed_conn, "relation")

        relation.set("connection", conn_name)
        relation.set("name", table_name)
        relation.set("table", f"[{table_name}]")
        relation.set("type", "table")
        for cols in relation.findall("columns"):
            relation.remove(cols)

        # 3. Update <object-graph> relation
        for og_rel in self._datasource.findall(".//object-graph//relation"):
            og_rel.set("connection", conn_name)
            og_rel.set("name", table_name)
            og_rel.set("table", f"[{table_name}]")
            og_rel.set("type", "table")
            for cols in og_rel.findall("columns"):
                og_rel.remove(cols)

        # 4. Cleanup old generic/excel connections and leftover fields
        excel_conn = self._datasource.find("connection[@class='excel-direct']")
        if excel_conn is not None:
            self._datasource.remove(excel_conn)
            
        old_cols = fed_conn.find("cols")
        if old_cols is not None:
            fed_conn.remove(old_cols)
            
        for c in self._datasource.findall("column"):
            self._datasource.remove(c)
            
        aliases = self._datasource.find("aliases")
        if aliases is not None:
            self._datasource.remove(aliases)

        # 6. Clean metadata-records
        for mr in self._datasource.findall(".//metadata-record"):
            mr.getparent().remove(mr)

        self._reinit_fields()
        return f"Configured MySQL connection to {server}/{dbname} (table: {table_name})"

    def set_tableauserver_connection(
        self,
        server: str,
        dbname: str,
        username: str,
        table_name: str,
        directory: str = "/dataserver",
        port: str = "82",
    ) -> str:
        """Configure the datasource to use a Tableau Server connection."""
        # 1. Remove all old connections
        for conn in self._datasource.findall("connection"):
            self._datasource.remove(conn)

        # 2. Add <repository-location>
        repo = self._datasource.find("repository-location")
        if repo is None:
            repo = etree.Element("repository-location")
            self._datasource.insert(0, repo)

        repo.set("derived-from", f"{directory}/{dbname}?rev=1.0")
        repo.set("id", dbname)
        repo.set("path", "/datasources")
        repo.set("revision", "1.0")

        # 3. Add <connection class='sqlproxy'>
        sqlproxy_conn = etree.Element("connection")
        channel = "https" if str(port) in ("443", "82") else "http"
        sqlproxy_conn.set("channel", channel)
        sqlproxy_conn.set("class", "sqlproxy")
        sqlproxy_conn.set("dbname", dbname)
        sqlproxy_conn.set("directory", directory)
        sqlproxy_conn.set("port", str(port))
        sqlproxy_conn.set("server", server)
        sqlproxy_conn.set("username", username)

        relation = etree.SubElement(sqlproxy_conn, "relation")
        relation.set("name", table_name)
        relation.set("table", f"[{table_name}]")
        relation.set("type", "table")

        # Insert after repository-location
        idx = list(self._datasource).index(repo)
        self._datasource.insert(idx + 1, sqlproxy_conn)

        # 4. Update <object-graph> relation
        for og_rel in self._datasource.findall(".//object-graph//relation"):
            if "connection" in og_rel.attrib:
                del og_rel.attrib["connection"]
            og_rel.set("name", table_name)
            og_rel.set("table", f"[{table_name}]")
            og_rel.set("type", "table")
            for cols in og_rel.findall("columns"):
                og_rel.remove(cols)
                
        # 5. Cleanup old fields and aliases
        for c in self._datasource.findall("column"):
            self._datasource.remove(c)
            
        aliases = self._datasource.find("aliases")
        if aliases is not None:
            self._datasource.remove(aliases)

        # 6. Clean metadata-records
        for mr in self._datasource.findall(".//metadata-record"):
            mr.getparent().remove(mr)

        self._reinit_fields()
        return f"Configured Tableau Server connection to {server}/{dbname} (table: {table_name})"

    def set_hyper_connection(
        self,
        filepath: str,
        table_name: str = "Extract",
        tables: Optional[List[dict]] = None,
    ) -> str:
        """Configure the datasource to use a local Hyper extract connection.

        Parameters
        ----------
        filepath : str
            Path to the ``.hyper`` file.
        table_name : str
            Table name for single-table mode (ignored when *tables* is given).
        tables : list[dict] | None
            For multi-table hyper files.  Each dict must have a ``"name"``
            key and may have an optional ``"columns"`` list of column-name
            strings.  The first entry is the *primary* table.
        """
        # 1. Update <connection class='federated'>
        fed_conn = self._datasource.find("connection[@class='federated']")
        if fed_conn is None:
            for old_conn in self._datasource.findall("connection"):
                self._datasource.remove(old_conn)
            fed_conn = etree.Element("connection")
            fed_conn.set("class", "federated")
            self._datasource.insert(0, fed_conn)

        # Update <named-connections>
        named_conns = fed_conn.find("named-connections")
        if named_conns is None:
            named_conns = etree.SubElement(fed_conn, "named-connections")
        else:
            for child in list(named_conns):
                named_conns.remove(child)

        conn_name = f"hyper.{_generate_uuid().strip('{}').lower()}"

        nc = etree.SubElement(named_conns, "named-connection")
        nc.set("caption", filepath.split("/")[-1].split("\\")[-1])
        nc.set("name", conn_name)

        hyper_conn = etree.SubElement(nc, "connection")
        hyper_conn.set("authentication", "auth-none")
        hyper_conn.set("author-locale", "en_US")
        hyper_conn.set("class", "hyper")
        hyper_conn.set("dbname", filepath)
        hyper_conn.set("default-settings", "yes")
        hyper_conn.set("schema", "Extract")
        hyper_conn.set("sslmode", "")
        hyper_conn.set("tablename", "Extract")
        hyper_conn.set("username", "")

        # Remove existing relation(s)
        for old_rel in fed_conn.findall("relation"):
            fed_conn.remove(old_rel)

        if tables and len(tables) > 1:
            # --- Multi-table mode ---
            self._set_hyper_multi_table(fed_conn, conn_name, tables)
        else:
            # --- Single-table mode (original behaviour) ---
            if tables and len(tables) == 1:
                table_name = tables[0]["name"]

            relation = etree.SubElement(fed_conn, "relation")
            relation.set("connection", conn_name)
            relation.set("name", table_name)
            relation.set("table", f"[Extract].[{table_name}]")
            relation.set("type", "table")

            # Update <object-graph> relation
            for og_rel in self._datasource.findall(".//object-graph//relation"):
                og_rel.set("connection", conn_name)
                og_rel.set("name", table_name)
                og_rel.set("table", f"[Extract].[{table_name}]")
                og_rel.set("type", "table")
                for cols in og_rel.findall("columns"):
                    og_rel.remove(cols)

        # Cleanup old generic/excel connections and leftover fields
        excel_conn = self._datasource.find("connection[@class='excel-direct']")
        if excel_conn is not None:
            self._datasource.remove(excel_conn)

        old_cols = fed_conn.find("cols")
        if old_cols is not None:
            fed_conn.remove(old_cols)

        for c in self._datasource.findall("column"):
            self._datasource.remove(c)

        aliases = self._datasource.find("aliases")
        if aliases is not None:
            self._datasource.remove(aliases)

        # Clean metadata-records
        for mr in self._datasource.findall(".//metadata-record"):
            mr.getparent().remove(mr)

        # Rebuild <column> and <metadata-record> elements from Hyper schema
        self._rebuild_fields_from_hyper(filepath, table_name, tables)

        self._reinit_fields()
        if tables and len(tables) > 1:
            names = ", ".join(t["name"] for t in tables)
            return f"Configured Hyper connection to {filepath} (tables: {names})"
        return f"Configured Hyper connection to {filepath} (table: {table_name})"

    # ------------------------------------------------------------------ #
    #  Multi-table helpers                                                #
    # ------------------------------------------------------------------ #

    def _set_hyper_multi_table(
        self,
        fed_conn: etree._Element,
        conn_name: str,
        tables: List[dict],
    ) -> None:
        """Build ``<relation type='collection'>`` for multi-table hyper files."""
        # -- Build the collection relation under fed_conn --
        collection = etree.SubElement(fed_conn, "relation")
        collection.set("type", "collection")
        for tbl in tables:
            child = etree.SubElement(collection, "relation")
            child.set("connection", conn_name)
            child.set("name", tbl["name"])
            child.set("table", f"[Extract].[{tbl['name']}]")
            child.set("type", "table")

        # -- Generate <cols> with <map> entries --
        primary = tables[0]
        primary_columns = set(primary.get("columns", []))

        cols_el = etree.SubElement(fed_conn, "cols")

        # Primary table maps: [Column] -> [PrimaryTable].[Column]
        for col_name in primary.get("columns", []):
            m = etree.SubElement(cols_el, "map")
            m.set("key", f"[{col_name}]")
            m.set("value", f"[{primary['name']}].[{col_name}]")

        # Non-primary tables
        for tbl in tables[1:]:
            for col_name in tbl.get("columns", []):
                m = etree.SubElement(cols_el, "map")
                if col_name in primary_columns:
                    # Overlapping column: suffix with (table_name)
                    m.set("key", f"[{col_name} ({tbl['name']})]")
                else:
                    m.set("key", f"[{col_name}]")
                m.set("value", f"[{tbl['name']}].[{col_name}]")

        # -- Update <object-graph> relations --
        for og_rel in self._datasource.findall(".//object-graph//relation"):
            old_name = og_rel.get("name", "")
            best_match = None
            # Find best match using exact or split-prefix
            for tbl in tables:
                base_name = tbl["name"].split("_")[0]
                if old_name == tbl["name"] or base_name in old_name:
                    best_match = tbl
                    break
            
            # Fallback
            if not best_match and tables:
                best_match = tables[0]
                
            if best_match:
                og_rel.set("connection", conn_name)
                og_rel.set("name", best_match["name"])
                og_rel.set("table", f"[Extract].[{best_match['name']}]")
                og_rel.set("type", "table")

    # ------------------------------------------------------------------ #
    #  Rebuild field metadata from Hyper schema                           #
    # ------------------------------------------------------------------ #

    # Map Hyper type strings to Tableau column metadata
    _HYPER_TYPE_MAP = {
        "DOUBLE": ("real", "measure", "quantitative", "5"),
        "FLOAT": ("real", "measure", "quantitative", "5"),
        "BIG_INT": ("integer", "measure", "quantitative", "20"),
        "INTEGER": ("integer", "measure", "quantitative", "20"),
        "INT": ("integer", "measure", "quantitative", "20"),
        "SMALL_INT": ("integer", "measure", "quantitative", "2"),
        "NUMERIC": ("real", "measure", "quantitative", "131"),
        "DATE": ("date", "dimension", "ordinal", "7"),
        "TIMESTAMP": ("datetime", "dimension", "ordinal", "135"),
        "TIMESTAMP_TZ": ("datetime", "dimension", "ordinal", "135"),
        "BOOL": ("boolean", "dimension", "nominal", "11"),
        "TEXT": ("string", "dimension", "nominal", "130"),
        "VARCHAR": ("string", "dimension", "nominal", "130"),
        "CHAR": ("string", "dimension", "nominal", "130"),
        "GEOGRAPHY": ("string", "dimension", "nominal", "130"),
    }

    def _rebuild_fields_from_hyper(
        self,
        filepath: str,
        table_name: str,
        tables: Optional[List[dict]],
    ) -> None:
        """Create <column> and <metadata-record> XML from Hyper file schema.

        This ensures the field registry can be populated after the old
        columns/metadata have been removed during connection setup.
        """
        try:
            schema_info = inspect_hyper_schema(filepath)
        except Exception:
            # If we can't read the Hyper file (e.g. it doesn't exist yet
            # during pipeline creation), skip — fields will be empty
            return

        if not schema_info.get("tables"):
            return

        # Determine which tables' columns to register
        if tables and len(tables) > 1:
            target_tables = schema_info["tables"]
        else:
            # Single-table: find matching table or use first
            target_tables = []
            for t in schema_info["tables"]:
                if t["name"] == table_name:
                    target_tables = [t]
                    break
            if not target_tables:
                target_tables = schema_info["tables"][:1]

        fed_conn = self._datasource.find("connection[@class='federated']")

        # Place <metadata-records> inside the federated <connection>, per XSD
        if fed_conn is not None:
            metadata_records = fed_conn.find("metadata-records")
            if metadata_records is None:
                metadata_records = etree.SubElement(fed_conn, "metadata-records")
        else:
            metadata_records = None

        ds_name = self._datasource.get("name", "")

        # Find the correct insertion point for <column> elements.
        # XSD order: ...aliases → column* → column-instance → ...layout → style...
        # Insert before the first element that must come after columns.
        _AFTER_COLUMN = {
            "column-instance", "group", "mapped-images", "drill-paths",
            "unlinked-server-hierarchies", "folders-common", "folders-parameters",
            "actions", "calculated-members", "extract", "layout", "style",
            "semantic-values", "date-options", "default-date-format",
            "default-sorts", "field-sort-info", "datasource-dependencies",
            "explainability", "filter", "object-graph",
        }
        col_insert_idx = len(self._datasource)
        for i, child in enumerate(self._datasource):
            if child.tag in _AFTER_COLUMN:
                col_insert_idx = i
                break

        for tbl in target_tables:
            suffix = f" ({tbl['name']})" if len(target_tables) > 1 else ""
            for col_info in tbl["columns"]:
                col_name = col_info["name"]
                hyper_type = col_info["type"].upper()

                # Normalize Hyper type (strip SqlType. prefix, nullable wrapper)
                for prefix in ("SQLTYPE.", "NULLABLE(", "("):
                    hyper_type = hyper_type.replace(prefix, "")
                hyper_type = hyper_type.rstrip(")")

                datatype, role, field_type, remote_type = self._HYPER_TYPE_MAP.get(
                    hyper_type, ("string", "dimension", "nominal", "130")
                )

                display_name = f"{col_name}{suffix}"
                local_name = f"[{display_name}]"

                # Create <column> element at the correct XSD position
                col_el = etree.Element("column")
                col_el.set("datatype", datatype)
                col_el.set("name", local_name)
                col_el.set("role", role)
                col_el.set("type", field_type)
                if suffix:
                    col_el.set("caption", display_name)
                self._datasource.insert(col_insert_idx, col_el)
                col_insert_idx += 1

                # Create <metadata-record class="column"> inside connection
                if metadata_records is not None:
                    mr = etree.SubElement(metadata_records, "metadata-record")
                    mr.set("class", "column")

                    remote_name_el = etree.SubElement(mr, "remote-name")
                    remote_name_el.text = col_name

                    remote_type_el = etree.SubElement(mr, "remote-type")
                    remote_type_el.text = remote_type

                    local_name_el = etree.SubElement(mr, "local-name")
                    local_name_el.text = local_name

                    parent_name_el = etree.SubElement(mr, "parent-name")
                    parent_name_el.text = f"[{ds_name}]"

                    local_type_el = etree.SubElement(mr, "local-type")
                    local_type_el.text = datatype

