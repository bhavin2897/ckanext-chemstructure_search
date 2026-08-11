import logging

from sqlalchemy import text

import ckan.model as model


log = logging.getLogger(__name__)

MOLECULE_NAMES_FIELD = "molecule_names"


def is_molecule_package(pkg_dict):
    """Recognize molecule packages in CKAN's flattened index dictionary."""
    return (
        pkg_dict.get("dataset_type") == "molecule"
        or pkg_dict.get("type") == "molecule"
    )


def get_alternate_names(package_id, inchi_key=None):
    """Return names for a package, falling back to its flattened InChIKey.

    ``molecule_rel_data.molecules_id`` is the deployed relation column.  It
    references ``rdk.molecules.id``; names use the cartridge-facing
    ``rdk.molecules.molecule_id`` identifier.
    """
    query = text("""
        WITH related_molecules AS (
            SELECT DISTINCT molecule.molecule_id
            FROM public.molecule_rel_data AS relation
            JOIN rdk.molecules AS molecule
              ON molecule.id = relation.molecules_id
            WHERE relation.package_id = :package_id
        ),
        resolved_molecules AS (
            SELECT molecule_id
            FROM related_molecules

            UNION ALL

            SELECT molecule.molecule_id
            FROM rdk.molecules AS molecule
            WHERE molecule.inchi_key = :inchi_key
              AND NOT EXISTS (SELECT 1 FROM related_molecules)
        )
        SELECT names.name
        FROM (
            SELECT DISTINCT molecule_name.name
            FROM resolved_molecules AS resolved
            JOIN rdk.molecule_names AS molecule_name
              ON molecule_name.molecule_id = resolved.molecule_id
            WHERE molecule_name.name IS NOT NULL
              AND btrim(molecule_name.name) <> ''
        ) AS names
        ORDER BY lower(names.name), names.name
    """)
    rows = model.Session.execute(query, {
        "package_id": package_id,
        "inchi_key": inchi_key,
    })
    return [row[0] for row in rows]


def add_molecule_names(pkg_dict):
    """Add database-backed names without disturbing the flattened document."""
    if not is_molecule_package(pkg_dict):
        return pkg_dict

    package_id = pkg_dict.get("id")
    if not package_id:
        return pkg_dict

    inchi_key = pkg_dict.get("inchi_key") or pkg_dict.get("extras_inchi_key")

    try:
        names = get_alternate_names(package_id, inchi_key)
    except Exception:
        log.exception(
            "CHEMSTRUCTURE failed to index molecule names for package %s",
            package_id,
        )
        return pkg_dict

    pkg_dict[MOLECULE_NAMES_FIELD] = names
    return pkg_dict
