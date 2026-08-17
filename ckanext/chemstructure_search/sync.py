import json
import logging

from sqlalchemy import text

import ckan.model as model


log = logging.getLogger(__name__)

MOLECULE_TYPE = "molecule"
ACTIVE_STATE = "active"
NAME_SOURCE = "CKAN"
NAME_TYPE = "alternate_name"


class MoleculeSyncError(Exception):
    """A package cannot be represented safely in the RDKit tables."""


def _package_ref(package_dict):
    return package_dict.get("id") or package_dict.get("name") or "unknown"


def _extras(package_dict):
    values = {}
    for item in package_dict.get("extras") or []:
        if isinstance(item, dict) and item.get("key"):
            values[item["key"]] = item.get("value")
    return values


def package_value(package_dict, key):
    value = package_dict.get(key)
    if value is None or value == "":
        value = _extras(package_dict).get(key)
    if isinstance(value, str):
        value = value.strip()
    return value if value != "" else None


def parse_alternate_names(value):
    if value is None or value == "":
        return []

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
        except (TypeError, ValueError):
            decoded = stripped
        value = decoded

    if not isinstance(value, (list, tuple)):
        value = [value]

    names = {}
    for name in value:
        if name is None:
            continue
        cleaned = str(name).strip()
        if cleaned and cleaned.casefold() not in names:
            names[cleaned.casefold()] = cleaned
    return list(names.values())


def _canonical_smiles(session, smiles):
    row = session.execute(
        text("""
            SELECT mol_to_smiles(
                mol_from_smiles(CAST(:smiles AS cstring))
            ) AS canonical_smiles
        """),
        {"smiles": smiles},
    ).fetchone()
    canonical = row[0] if row else None
    if not canonical:
        raise MoleculeSyncError("invalid SMILES")
    return canonical


def _existing_molecule_id(session, inchi_code):
    row = session.execute(
        text("""
            SELECT molecule_id
            FROM rdk.molecules
            WHERE inchi_code = :inchi_code
        """),
        {"inchi_code": inchi_code},
    ).fetchone()
    return row[0] if row else None


def _upsert_molecule(session, values):
    row = session.execute(
        text("""
            INSERT INTO rdk.molecules (
    molecule,
    canonical_smiles,
    inchi_key,
    inchi_code,
    mol_formula,
    exact_mass
)
VALUES (
    mol_from_smiles(CAST(:canonical_smiles AS cstring)),
    :canonical_smiles,
    :inchi_key,
    :inchi_code,
    :mol_formula,
    :exact_mass
)
ON CONFLICT (inchi_code) DO UPDATE SET
    molecule = EXCLUDED.molecule,
    canonical_smiles = EXCLUDED.canonical_smiles,
    inchi_key = COALESCE(
        NULLIF(EXCLUDED.inchi_key, ''),
        rdk.molecules.inchi_key
    ),
    mol_formula = COALESCE(
        NULLIF(EXCLUDED.mol_formula, ''),
        rdk.molecules.mol_formula
    ),
    exact_mass = COALESCE(
        EXCLUDED.exact_mass,
        rdk.molecules.exact_mass
    )
RETURNING molecule_id
        """),
        values,
    ).fetchone()
    if not row:
        raise MoleculeSyncError("RDKit molecule upsert returned no row")
    return row[0]


def _upsert_fingerprints(session, molecule_id):
    session.execute(
        text("""
            INSERT INTO rdk.fingerprints (molecule_id, mfp2, ffp2)
            SELECT molecule_id, morganbv_fp(molecule),
                   featmorganbv_fp(molecule)
            FROM rdk.molecules
            WHERE molecule_id = :molecule_id
            ON CONFLICT (molecule_id) DO UPDATE SET
                mfp2 = EXCLUDED.mfp2,
                ffp2 = EXCLUDED.ffp2
        """),
        {"molecule_id": molecule_id},
    )


def _upsert_names(session, molecule_id, names):
    for name in names:
        session.execute(
            text("""
                INSERT INTO rdk.molecule_names (
                    molecule_id, name, type, source
                )
                VALUES (:molecule_id, :name, :name_type, :source)
                ON CONFLICT (molecule_id, name) DO NOTHING
            """),
            {
                "molecule_id": molecule_id,
                "name": name,
                "name_type": NAME_TYPE,
                "source": NAME_SOURCE,
            },
        )


def sync_molecule_package(context, package_dict, session=None):
    """Idempotently copy one active CKAN molecule package into ``rdk.*``.

    The caller owns the transaction. No CKAN action, network request, commit,
    rollback, or indexing operation is performed here.
    """
    if package_dict.get("type") != MOLECULE_TYPE:
        return {"status": "skipped", "reason": "not a molecule package"}
    if package_dict.get("state", ACTIVE_STATE) != ACTIVE_STATE:
        return {"status": "skipped", "reason": "package is not active"}

    smiles = package_value(package_dict, "smiles")
    inchi_code = package_value(package_dict, "inchi")
    if not smiles:
        raise MoleculeSyncError("missing SMILES")
    if not inchi_code:
        raise MoleculeSyncError("missing InChI")

    session = session or model.Session
    canonical_smiles = _canonical_smiles(session, smiles)
    existing_id = _existing_molecule_id(session, inchi_code)
    exact_mass = package_value(package_dict, "exactmass")
    if exact_mass is not None:
        try:
            exact_mass = float(exact_mass)
        except (TypeError, ValueError):
            raise MoleculeSyncError("invalid exact mass")

    molecule_id = _upsert_molecule(session, {
        "canonical_smiles": canonical_smiles,
        "inchi_key": package_value(package_dict, "inchi_key"),
        "inchi_code": inchi_code,
        "mol_formula": package_value(package_dict, "mol_formula"),
        "exact_mass": exact_mass,
    })
    _upsert_fingerprints(session, molecule_id)
    names = parse_alternate_names(
        package_value(package_dict, "alternate_name")
    )
    _upsert_names(session, molecule_id, names)

    return {
        "status": "updated" if existing_id is not None else "created",
        "molecule_id": molecule_id,
        "names": len(names),
    }


def sync_molecule_package_safely(context, package_dict, session=None):
    """Lifecycle-safe wrapper: report one bad molecule without raising."""
    package_ref = _package_ref(package_dict)
    try:
        with (session or model.Session).begin_nested():
            return sync_molecule_package(context, package_dict, session=session)
    except MoleculeSyncError as error:
        reason = str(error)
    except Exception:
        reason = "database synchronization error"

    log.warning(
        "CHEMSTRUCTURE molecule_sync package=%s status=failed reason=%s",
        package_ref,
        reason,
    )
    return {"status": "failed", "reason": reason}
