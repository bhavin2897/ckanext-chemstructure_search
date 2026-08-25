import base64
import logging
import re
from io import BytesIO

from sqlalchemy import text

from rdkit import Chem
from rdkit.Chem import Draw
from rdkit import RDLogger

import ckan.model as model
import ckan.plugins.toolkit as toolkit


RDLogger.DisableLog("rdApp.error")

log = logging.getLogger(__name__)

VALID_SEARCH_MODES = ("exact", "substructure", "smarts", "similarity")
FINGERPRINT_RANKED_MODES = ("similarity",)
DEFAULT_ROWS = 50
DEFAULT_THRESHOLD = 0.25

RDK_MOLECULES = "rdk.molecules"
RDK_FINGERPRINTS = "rdk.fingerprints"
PACKAGE_TABLE = "public.package"
PACKAGE_EXTRA_TABLE = "public.package_extra"

STRING_LIKE_COLUMN_TYPES = set([
    "text",
    "character varying",
    "character",
    "uuid",
])


def _validation_error(field, message):
    raise toolkit.ValidationError({
        field: [message]
    })


def _row_mapping(row):
    if isinstance(row, dict):
        return row

    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return dict(mapping)

    return dict(row)


def _scalar(sql, params=None):
    return model.Session.execute(text(sql), params or {}).scalar()


def _quote_identifier(value):
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value or ""):
        _validation_error(
            "rdkit",
            "RDKit function lookup returned an unsafe SQL identifier.",
        )

    return '"{}"'.format(value)


def _qualified_function(schema, name):
    return "{}.{}".format(_quote_identifier(schema), _quote_identifier(name))


def _validate_mode(mode):
    mode = mode or "similarity"

    if mode not in VALID_SEARCH_MODES:
        _validation_error(
            "mode",
            "Mode must be one of: exact, substructure, smarts, similarity.",
        )

    return mode


def _validate_threshold(threshold):
    if threshold is None or threshold == "":
        threshold = DEFAULT_THRESHOLD

    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        _validation_error(
            "threshold",
            "Threshold must be a number from 0.0 to 1.0.",
        )

    if threshold < 0.0 or threshold > 1.0:
        _validation_error(
            "threshold",
            "Threshold must be a number from 0.0 to 1.0.",
        )

    return threshold


def _validate_rows(rows):
    if rows is None or rows == "":
        return None

    try:
        rows = int(rows)
    except (TypeError, ValueError):
        _validation_error("rows", "Rows must be a non-negative integer.")

    if rows < 0:
        _validation_error("rows", "Rows must be a non-negative integer.")

    return rows


def _validate_query(query):
    if not query:
        _validation_error("query", "SMILES or SMARTS query is required.")

    return query


def _fetch_table_names():
    row = model.Session.execute(text("""
        SELECT
            to_regclass(:molecules) AS molecules,
            to_regclass(:fingerprints) AS fingerprints,
            to_regclass(:package_table) AS package_table,
            to_regclass(:package_extra_table) AS package_extra_table
    """), {
        "molecules": RDK_MOLECULES,
        "fingerprints": RDK_FINGERPRINTS,
        "package_table": PACKAGE_TABLE,
        "package_extra_table": PACKAGE_EXTRA_TABLE,
    }).fetchone()

    return _row_mapping(row)


def _fetch_columns():
    rows = model.Session.execute(text("""
        SELECT
            table_schema,
            table_name,
            column_name,
            data_type
        FROM information_schema.columns
        WHERE (table_schema, table_name) IN (
            ('rdk', 'molecules'),
            ('rdk', 'fingerprints'),
            ('public', 'package'),
            ('public', 'package_extra')
        )
    """)).fetchall()

    columns = {}

    for row in rows:
        item = _row_mapping(row)
        key = (item["table_schema"], item["table_name"])
        columns.setdefault(key, {})[item["column_name"]] = item["data_type"]

    return columns


def _fetch_function_refs():
    rows = model.Session.execute(text("""
        SELECT
            n.nspname AS schema_name,
            p.proname AS function_name
        FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n
          ON n.oid = p.pronamespace
        WHERE p.proname IN (
            'mol_from_smiles',
            'mol_to_smiles',
            'qmol_from_smarts',
            'mol_from_smarts',
            'morganbv_fp',
            'tanimoto_sml'
        )
        ORDER BY
            CASE p.proname
                WHEN 'qmol_from_smarts' THEN 0
                WHEN 'mol_from_smarts' THEN 1
                ELSE 2
            END,
            n.nspname
    """)).fetchall()

    functions = {}

    for row in rows:
        item = _row_mapping(row)
        name = item["function_name"]

        if name not in functions:
            functions[name] = _qualified_function(
                item["schema_name"],
                item["function_name"],
            )

    return functions


def _fetch_similarity_operator_support():
    rows = model.Session.execute(text("""
        SELECT DISTINCT o.oprname
        FROM pg_catalog.pg_operator o
        WHERE o.oprname IN ('%', '<%>')
          AND (
            pg_catalog.format_type(o.oprleft, NULL) ILIKE '%bfp%'
            OR pg_catalog.format_type(o.oprright, NULL) ILIKE '%bfp%'
            OR pg_catalog.format_type(o.oprleft, NULL) ILIKE '%mfp%'
            OR pg_catalog.format_type(o.oprright, NULL) ILIKE '%mfp%'
          )
    """)).fetchall()

    operators = set()

    for row in rows:
        item = _row_mapping(row)
        operators.add(item["oprname"])

    return {
        "threshold": "%" in operators,
        "knn": "<%>" in operators,
    }


def _require_columns(columns, table_key, required, error_field):
    table_columns = columns.get(table_key, {})
    missing = [column for column in required if column not in table_columns]

    if missing:
        _validation_error(
            error_field,
            "Missing required columns on {}.{}: {}.".format(
                table_key[0],
                table_key[1],
                ", ".join(sorted(missing)),
            ),
        )


def _column_type(columns, table_key, column):
    return columns.get(table_key, {}).get(column)


def _direct_mapping_available(columns):
    molecule_id_type = _column_type(
        columns,
        ("rdk", "molecules"),
        "molecule_id",
    )
    package_id_type = _column_type(columns, ("public", "package"), "id")

    if not molecule_id_type or not package_id_type:
        return False

    if molecule_id_type == package_id_type:
        return True

    return molecule_id_type in STRING_LIKE_COLUMN_TYPES and (
        package_id_type in STRING_LIKE_COLUMN_TYPES
    )


def _select_package_mapping(columns):
    if _direct_mapping_available(columns):
        return "package_id"

    _require_columns(
        columns,
        ("public", "package_extra"),
        ("package_id", "key", "value"),
        "schema",
    )

    if "inchi_key" not in columns.get(("rdk", "molecules"), {}):
        _validation_error(
            "schema",
            "Cannot map RDKit molecules to CKAN packages: no compatible "
            "molecule_id/package.id mapping and rdk.molecules.inchi_key "
            "is missing.",
        )

    return "inchi_key"


def _inspect_rdkit_schema(mode):
    try:
        rdkit_available = _scalar("""
            SELECT EXISTS(
                SELECT 1
                FROM pg_extension
                WHERE extname = 'rdkit'
            )
        """)

        if not rdkit_available:
            _validation_error(
                "rdkit",
                "PostgreSQL RDKit extension is not installed in this "
                "database.",
            )

        tables = _fetch_table_names()

        if not tables.get("molecules"):
            _validation_error(
                "schema",
                "Required table rdk.molecules is unavailable.",
            )

        if not tables.get("package_table"):
            _validation_error(
                "schema",
                "Required CKAN table public.package is unavailable.",
            )

        if not tables.get("package_extra_table"):
            _validation_error(
                "schema",
                "Required CKAN table public.package_extra is unavailable.",
            )

        if mode in FINGERPRINT_RANKED_MODES and not tables.get(
            "fingerprints"
        ):
            _validation_error(
                "fingerprints",
                "Required table rdk.fingerprints is unavailable for "
                "{} search.".format(mode),
            )

        columns = _fetch_columns()

        _require_columns(
            columns,
            ("rdk", "molecules"),
            ("molecule_id", "molecule", "canonical_smiles"),
            "schema",
        )
        _require_columns(
            columns,
            ("public", "package"),
            ("id", "name", "title", "type", "state"),
            "schema",
        )

        if mode in FINGERPRINT_RANKED_MODES:
            _require_columns(
                columns,
                ("rdk", "fingerprints"),
                ("molecule_id", "mfp2"),
                "fingerprints",
            )

        mapping = _select_package_mapping(columns)
        functions = _fetch_function_refs()

        required_functions = ["mol_from_smiles", "mol_to_smiles"]

        if mode in FINGERPRINT_RANKED_MODES:
            required_functions.extend(["morganbv_fp", "tanimoto_sml"])

        missing_functions = [
            function_name
            for function_name in required_functions
            if function_name not in functions
        ]

        if missing_functions:
            _validation_error(
                "rdkit",
                "Missing required RDKit cartridge functions: {}.".format(
                    ", ".join(sorted(missing_functions)),
                ),
            )

        smarts_function = None

        if mode in ("smarts", "substructure"):
            smarts_function = functions.get("qmol_from_smarts") or (
                functions.get("mol_from_smarts")
            )

            if not smarts_function:
                _validation_error(
                    "rdkit",
                    "No SMARTS-compatible RDKit cartridge function is "
                    "installed.",
                )

        return {
            "columns": columns,
            "mapping": mapping,
            "functions": functions,
            "smarts_function": smarts_function,
            "package_extra_has_state": (
                "state" in columns.get(("public", "package_extra"), {})
            ),
            "similarity_operators": _fetch_similarity_operator_support(),
        }

    except toolkit.ValidationError:
        raise
    except Exception as error:
        log.exception(
            "CHEMSTRUCTURE failed to inspect PostgreSQL RDKit schema"
        )
        _validation_error(
            "database",
            "Could not inspect PostgreSQL RDKit schema: {}.".format(error),
        )


def _validate_smiles_query_in_database(query, functions):
    sql = text("""
        WITH query_molecule AS (
            SELECT {mol_from_smiles}(CAST(:query AS cstring)) AS molecule
        )
        SELECT {mol_to_smiles}(molecule) AS query_canonical_smiles
        FROM query_molecule
        WHERE molecule IS NOT NULL
    """.format(
        mol_from_smiles=functions["mol_from_smiles"],
        mol_to_smiles=functions["mol_to_smiles"],
    ))

    try:
        row = model.Session.execute(sql, {"query": query}).fetchone()
    except Exception as error:
        log.exception("CHEMSTRUCTURE invalid SMILES query")
        _validation_error(
            "smiles",
            "Invalid SMILES. PostgreSQL RDKit could not parse the query: "
            "{}.".format(error),
        )

    if not row:
        _validation_error(
            "smiles",
            "Invalid SMILES. PostgreSQL RDKit could not parse the query.",
        )

    return _row_mapping(row).get("query_canonical_smiles")


def _validate_smarts_query_in_database(query, smarts_function):
    sql = text("""
        WITH query_pattern AS (
            SELECT {smarts_function}(CAST(:query AS cstring)) AS pattern
        )
        SELECT TRUE AS valid
        FROM query_pattern
        WHERE pattern IS NOT NULL
    """.format(smarts_function=smarts_function))

    try:
        row = model.Session.execute(sql, {"query": query}).fetchone()
    except Exception as error:
        log.exception("CHEMSTRUCTURE invalid SMARTS query")
        _validation_error(
            "smarts",
            "Invalid SMARTS. PostgreSQL RDKit could not parse the query: "
            "{}.".format(error),
        )

    if not row:
        _validation_error(
            "smarts",
            "Invalid SMARTS. PostgreSQL RDKit could not parse the query.",
        )

    return None


def _generalize_kekule_ring_bonds(query):
    """Make Ketcher Kekule ring SMARTS compatible with aromatic targets.

    Ketcher cannot aromatize a query structure containing atom lists or other
    query features.  It consequently exports rings using alternating single
    and double bonds, while RDKit stores aromatic molecules with aromatic bond
    types.  Generalize only alternating ring bonds to SMARTS ``~`` bonds.
    Exocyclic bonds and all atom predicates remain unchanged.
    """
    pattern = Chem.MolFromSmarts(query)

    if pattern is None:
        return query

    try:
        Chem.GetSymmSSSR(pattern)
        generalized_bond_indexes = set()

        for atom_ring in pattern.GetRingInfo().AtomRings():
            ring_bonds = []

            for index, atom_index in enumerate(atom_ring):
                next_atom_index = atom_ring[(index + 1) % len(atom_ring)]
                bond = pattern.GetBondBetweenAtoms(
                    atom_index,
                    next_atom_index,
                )
                if bond is None:
                    ring_bonds = []
                    break
                ring_bonds.append(bond)

            bond_types = [bond.GetBondType() for bond in ring_bonds]
            if not ring_bonds or Chem.rdchem.BondType.DOUBLE not in bond_types:
                continue
            if any(
                bond_type not in (
                    Chem.rdchem.BondType.SINGLE,
                    Chem.rdchem.BondType.DOUBLE,
                )
                for bond_type in bond_types
            ):
                continue

            # Only generalize a genuinely alternating Kekule ring. This
            # avoids weakening ordinary rings that merely contain a double
            # bond, such as cyclohexene.
            if any(
                bond_types[index] == bond_types[(index + 1) % len(bond_types)]
                for index in range(len(bond_types))
            ):
                continue

            generalized_bond_indexes.update(
                bond.GetIdx() for bond in ring_bonds
            )

        if not generalized_bond_indexes:
            return query

        editable_pattern = Chem.RWMol(pattern)

        for bond_index in generalized_bond_indexes:
            # Changing BondType on a QueryBond changes only its display type;
            # its original BondOrder query remains active. Replace the whole
            # query bond so MolToSmarts emits and PostgreSQL evaluates ``~``.
            editable_pattern.ReplaceBond(
                bond_index,
                Chem.BondFromSmarts("~"),
                preserveProps=False,
            )

        return Chem.MolToSmarts(editable_pattern)
    except Exception:
        log.exception(
            "CHEMSTRUCTURE failed to generalize Ketcher ring SMARTS"
        )
        return query


def _validate_structure_query_in_database(query, mode, metadata):
    if mode in ("smarts", "substructure"):
        return _validate_smarts_query_in_database(
            query,
            metadata["smarts_function"],
        )

    return _validate_smiles_query_in_database(query, metadata["functions"])


def _package_join_sql(mapping, package_extra_has_state):
    if mapping == "package_id":
        return """
            JOIN "package" p
              ON p.id::text = h.molecule_id::text
        """

    package_extra_state_filter = ""

    if package_extra_has_state:
        package_extra_state_filter = "AND pe.state = 'active'"

    return """
        JOIN package_extra pe
          ON pe.value = h.inchi_key
         AND pe.key = 'inchi_key'
         {package_extra_state_filter}
        JOIN "package" p
          ON p.id = pe.package_id
    """.format(package_extra_state_filter=package_extra_state_filter)


def _hit_inchi_sql(mapping):
    if mapping == "inchi_key":
        return "m.inchi_key"

    return "NULL::text AS inchi_key"


def _limit_sql(rows):
    if rows is None:
        return ""

    return "LIMIT :rows"


def _result_order_sql(mode):
    if mode in FINGERPRINT_RANKED_MODES:
        return "ORDER BY similarity DESC NULLS LAST, name"

    return "ORDER BY name"


def _build_exact_sql(metadata, rows):
    functions = metadata["functions"]
    package_join = _package_join_sql(
        metadata["mapping"],
        metadata["package_extra_has_state"],
    )

    return text("""
        WITH query_molecule AS (
            SELECT {mol_from_smiles}(CAST(:query AS cstring)) AS molecule
        ),
        hits AS (
            SELECT
                m.molecule_id,
                {inchi_sql},
                m.canonical_smiles,
                NULL::double precision AS similarity
            FROM rdk.molecules m
            CROSS JOIN query_molecule q
            WHERE m.molecule @= q.molecule
        ),
        joined AS (
            SELECT DISTINCT ON (p.id)
                p.id,
                p.name,
                p.title,
                h.canonical_smiles,
                h.similarity
            FROM hits h
            {package_join}
            WHERE p.type = 'molecule'
              AND p.state = 'active'
            ORDER BY p.id, p.name
        )
        SELECT
            id,
            name,
            title,
            canonical_smiles,
            similarity
        FROM joined
        {order_sql}
        {limit_sql}
    """.format(
        mol_from_smiles=functions["mol_from_smiles"],
        inchi_sql=_hit_inchi_sql(metadata["mapping"]),
        package_join=package_join,
        order_sql=_result_order_sql("exact"),
        limit_sql=_limit_sql(rows),
    ))


def _build_substructure_sql(metadata, rows):
    functions = metadata["functions"]
    package_join = _package_join_sql(
        metadata["mapping"],
        metadata["package_extra_has_state"],
    )

    return text("""
        WITH query_molecule AS (
            SELECT {mol_from_smiles}(CAST(:query AS cstring)) AS molecule
        ),
        query_data AS (
            SELECT
                molecule,
                {morganbv_fp}(molecule) AS query_fingerprint
            FROM query_molecule
            WHERE molecule IS NOT NULL
        ),
        hits AS (
            SELECT
                m.molecule_id,
                {inchi_sql},
                m.canonical_smiles,
                {tanimoto_sml}(
                    q.query_fingerprint,
                    f.mfp2
                ) AS similarity
            FROM rdk.molecules m
            LEFT JOIN rdk.fingerprints f
              ON f.molecule_id = m.molecule_id
            CROSS JOIN query_data q
            WHERE m.molecule @> q.molecule
        ),
        joined AS (
            SELECT DISTINCT ON (p.id)
                p.id,
                p.name,
                p.title,
                h.canonical_smiles,
                h.similarity
            FROM hits h
            {package_join}
            WHERE p.type = 'molecule'
              AND p.state = 'active'
            ORDER BY p.id, h.similarity DESC NULLS LAST, p.name
        )
        SELECT
            id,
            name,
            title,
            canonical_smiles,
            similarity
        FROM joined
        {order_sql}
        {limit_sql}
    """.format(
        mol_from_smiles=functions["mol_from_smiles"],
        morganbv_fp=functions["morganbv_fp"],
        tanimoto_sml=functions["tanimoto_sml"],
        inchi_sql=_hit_inchi_sql(metadata["mapping"]),
        package_join=package_join,
        order_sql=_result_order_sql("substructure"),
        limit_sql=_limit_sql(rows),
    ))


def _build_smarts_sql(metadata, rows):
    package_join = _package_join_sql(
        metadata["mapping"],
        metadata["package_extra_has_state"],
    )

    return text("""
        WITH query_pattern AS (
            SELECT {smarts_function}(CAST(:query AS cstring)) AS pattern
        ),
        hits AS (
            SELECT
                m.molecule_id,
                {inchi_sql},
                m.canonical_smiles,
                NULL::double precision AS similarity
            FROM rdk.molecules m
            CROSS JOIN query_pattern q
            WHERE m.molecule @> q.pattern
        ),
        joined AS (
            SELECT DISTINCT ON (p.id)
                p.id,
                p.name,
                p.title,
                h.canonical_smiles,
                h.similarity
            FROM hits h
            {package_join}
            WHERE p.type = 'molecule'
              AND p.state = 'active'
            ORDER BY p.id, p.name
        )
        SELECT
            id,
            name,
            title,
            canonical_smiles,
            similarity
        FROM joined
        {order_sql}
        {limit_sql}
    """.format(
        smarts_function=metadata["smarts_function"],
        inchi_sql=_hit_inchi_sql(metadata["mapping"]),
        package_join=package_join,
        order_sql=_result_order_sql("smarts"),
        limit_sql=_limit_sql(rows),
    ))


def _build_similarity_sql(metadata, rows):
    functions = metadata["functions"]
    operators = metadata["similarity_operators"]
    package_join = _package_join_sql(
        metadata["mapping"],
        metadata["package_extra_has_state"],
    )

    threshold_filter = ""
    knn_order = ""

    if operators.get("threshold"):
        threshold_filter = "AND f.mfp2 % q.query_fingerprint"

    if operators.get("knn"):
        knn_order = "ORDER BY f.mfp2 <%> q.query_fingerprint"

    return text("""
        WITH query_molecule AS (
            SELECT {mol_from_smiles}(CAST(:query AS cstring)) AS molecule
        ),
        query_fingerprint AS (
            SELECT
                {morganbv_fp}(molecule) AS query_fingerprint
            FROM query_molecule
            WHERE molecule IS NOT NULL
        ),
        hits AS (
            SELECT
                m.molecule_id,
                {inchi_sql},
                m.canonical_smiles,
                {tanimoto_sml}(q.query_fingerprint, f.mfp2) AS similarity
            FROM rdk.molecules m
            JOIN rdk.fingerprints f
              ON f.molecule_id = m.molecule_id
            CROSS JOIN query_fingerprint q
            WHERE {tanimoto_sml}(q.query_fingerprint, f.mfp2) >= :threshold
              {threshold_filter}
            {knn_order}
        ),
        joined AS (
            SELECT DISTINCT ON (p.id)
                p.id,
                p.name,
                p.title,
                h.canonical_smiles,
                h.similarity
            FROM hits h
            {package_join}
            WHERE p.type = 'molecule'
              AND p.state = 'active'
            ORDER BY p.id, h.similarity DESC NULLS LAST, p.name
        )
        SELECT
            id,
            name,
            title,
            canonical_smiles,
            similarity
        FROM joined
        {order_sql}
        {limit_sql}
    """.format(
        mol_from_smiles=functions["mol_from_smiles"],
        morganbv_fp=functions["morganbv_fp"],
        tanimoto_sml=functions["tanimoto_sml"],
        inchi_sql=_hit_inchi_sql(metadata["mapping"]),
        threshold_filter=threshold_filter,
        knn_order=knn_order,
        package_join=package_join,
        order_sql=_result_order_sql("similarity"),
        limit_sql=_limit_sql(rows),
    ))


def _build_search_sql(mode, metadata, rows):
    if mode == "exact":
        return _build_exact_sql(metadata, rows)

    if mode == "substructure":
        return _build_smarts_sql(metadata, rows)

    if mode == "smarts":
        return _build_smarts_sql(metadata, rows)

    if mode == "similarity":
        return _build_similarity_sql(metadata, rows)

    _validation_error("mode", "Unsupported search mode.")


def _set_transaction_local_tanimoto_threshold(threshold):
    model.Session.execute(text("""
        SELECT set_config('rdkit.tanimoto_threshold', :threshold, true)
    """), {
        "threshold": str(threshold),
    })


def _execute_structure_sql(sql, params):
    try:
        return model.Session.execute(sql, params).fetchall()
    except Exception as error:
        log.exception("CHEMSTRUCTURE PostgreSQL RDKit search failed")
        _validation_error(
            "database",
            "PostgreSQL RDKit structure search failed: {}.".format(error),
        )


def _format_result(row, mode):
    item = _row_mapping(row)
    result = {
        "id": item.get("id"),
        "name": item.get("name"),
        "title": item.get("title"),
        "canonical_smiles": item.get("canonical_smiles"),
        "mode": mode,
    }

    if mode in FINGERPRINT_RANKED_MODES:
        similarity = item.get("similarity")
        result["similarity"] = (
            None if similarity is None else round(float(similarity), 4)
        )

    return result


def _run_structure_search_cartridge(query, mode, threshold, rows):
    query = _validate_query(query)
    mode = _validate_mode(mode)
    threshold = _validate_threshold(threshold)
    rows = _validate_rows(rows)

    metadata = _inspect_rdkit_schema(mode)
    database_query = (
        _generalize_kekule_ring_bonds(query)
        if mode == "substructure"
        else query
    )
    if database_query != query:
        log.info(
            "CHEMSTRUCTURE generalized substructure SMARTS original=%s query=%s",
            query,
            database_query,
        )
    query_canonical_smiles = _validate_structure_query_in_database(
        database_query,
        mode,
        metadata,
    )

    if mode == "similarity" and (
        metadata["similarity_operators"].get("threshold")
    ):
        _set_transaction_local_tanimoto_threshold(threshold)

    sql = _build_search_sql(mode, metadata, rows)
    params = {
        "query": database_query,
        "threshold": threshold,
    }

    if rows is not None:
        params["rows"] = rows

    result_rows = _execute_structure_sql(sql, params)
    results = [_format_result(row, mode) for row in result_rows]

    return {
        "count": len(results),
        "query": query,
        "query_canonical_smiles": query_canonical_smiles,
        "threshold": threshold if mode == "similarity" else None,
        "source": "postgresql_cartridge",
        "solr_used": False,
        "results": results,
    }


def run_structure_search(
    query,
    mode="similarity",
    threshold=DEFAULT_THRESHOLD,
    rows=None,
):
    """
    Run a PostgreSQL RDKit cartridge search.

    Stored molecule matching is intentionally performed only in PostgreSQL.
    There is no Python RDKit candidate scan or fallback path here.
    """

    return _run_structure_search_cartridge(
        query=query,
        mode=mode,
        threshold=threshold,
        rows=rows,
    )


def chemstructure_rdkit_search(context, data_dict):
    """
    Solr-independent RDKit structure search.

    Endpoint:
        /api/3/action/chemstructure_rdkit_search
    """

    toolkit.check_access("package_search", context, data_dict)

    query = data_dict.get("query") or data_dict.get("smiles")
    mode = data_dict.get("mode", "similarity")
    threshold = data_dict.get("threshold", DEFAULT_THRESHOLD)
    rows = data_dict.get("rows", DEFAULT_ROWS)

    return run_structure_search(
        query=query,
        mode=mode,
        threshold=threshold,
        rows=rows,
    )


def chemstructure_exact_search(context, data_dict):
    """
    Compatibility action for exact and SMARTS searches.

    Endpoint:
        /api/3/action/chemstructure_exact_search
    """

    toolkit.check_access("package_search", context, data_dict)

    query = data_dict.get("smiles") or data_dict.get("query")
    mode = data_dict.get("mode", "exact")
    threshold = data_dict.get("threshold", DEFAULT_THRESHOLD)
    rows = data_dict.get("rows", DEFAULT_ROWS)

    return run_structure_search(
        query=query,
        mode=mode,
        threshold=threshold,
        rows=rows,
    )


def chemstructure_render_query_image(context, data_dict):
    """
    Render a query molecule image from SMILES or SMARTS.

    Endpoint:
        /api/3/action/chemstructure_render_query_image
    """

    toolkit.check_access("package_search", context, data_dict)

    query = data_dict.get("smiles")
    query = query or data_dict.get("structure_query")
    query = query or data_dict.get("query")

    mode = data_dict.get("mode") or data_dict.get("structure_mode")

    if not query:
        raise toolkit.ValidationError({
            "smiles": ["SMILES query is required."]
        })

    is_smarts = mode in ("smarts", "substructure")
    mol = Chem.MolFromSmarts(query) if is_smarts else Chem.MolFromSmiles(query)

    if mol is None:
        raise toolkit.ValidationError({
            "query": [
                "Invalid {}. RDKit could not parse the query structure.".format(
                    "SMARTS" if is_smarts else "SMILES"
                )
            ]
        })

    try:
        image = Draw.MolToImage(mol, size=(260, 180))
        buffer = BytesIO()
        image.save(buffer, format="PNG")

        image_base64 = base64.b64encode(buffer.getvalue()).decode("ascii")

        return {
            "image_base64": image_base64,
            "format": "png",
            "query": query,
        }

    except Exception:
        log.exception("CHEMSTRUCTURE failed to render query image")
        raise toolkit.ValidationError({
            "image": ["Could not render query molecule image."]
        })
