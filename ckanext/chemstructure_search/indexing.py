import logging

from sqlalchemy import text

import ckan.model as model


log = logging.getLogger(__name__)


def get_molecule_synonyms(inchi_key):
    """Return the non-empty names belonging to an RDKit molecule."""
    query = text("""
        SELECT DISTINCT mn.name
        FROM rdk.molecules AS rm
        JOIN rdk.molecule_names AS mn
          ON mn.molecule_id = rm.molecule_id
        WHERE rm.inchi_key = :inchi_key
          AND mn.name IS NOT NULL
          AND btrim(mn.name) <> ''
        ORDER BY mn.name
    """)
    rows = model.Session.execute(query, {"inchi_key": inchi_key})
    return [row[0] for row in rows]


def _deduplicate_synonyms(names):
    """Strip and deduplicate names case-insensitively in stable order."""
    unique = {}

    for name in names:
        if name is None:
            continue

        stripped_name = name.strip()
        if not stripped_name:
            continue

        key = stripped_name.casefold()
        if key not in unique:
            unique[key] = stripped_name

    return sorted(unique.values(), key=lambda value: (value.casefold(), value))


def add_molecule_synonyms(search_data):
    """Add RDKit synonyms to CKAN's normal searchable catch-all field."""
    if (
        search_data.get("dataset_type") != "molecule"
        and search_data.get("type") != "molecule"
    ):
        return search_data

    # PackageSearchIndex has already discarded deleted packages before this
    # hook. Explicitly avoid indexing any other non-active package state too.
    if search_data.get("state") != "active":
        return search_data

    inchi_key = (
        search_data.get("inchi_key")
        or search_data.get("extras_inchi_key")
    )
    if not inchi_key:
        return search_data

    package_ref = search_data.get("name") or search_data.get("id") or "unknown"

    try:
        synonyms = _deduplicate_synonyms(get_molecule_synonyms(inchi_key))
    except Exception:
        log.exception(
            "CHEMSTRUCTURE synonym indexing failed package=%s inchi_key=%s",
            package_ref,
            inchi_key,
        )
        return search_data

    if synonyms:
        existing_text = search_data.get("text") or ""
        if isinstance(existing_text, (list, tuple)):
            existing_text = " ".join(str(value) for value in existing_text)
        synonym_text = " ".join(synonyms)
        search_data["text"] = "{} {}".format(
            existing_text,
            synonym_text,
        ).strip()

    log.info(
        "CHEMSTRUCTURE synonym indexing package=%s inchi_key=%s count=%s",
        package_ref,
        inchi_key,
        len(synonyms),
    )
    return search_data
