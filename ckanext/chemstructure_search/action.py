import base64
import logging

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

import ckan.model as model
from ckan.model import Package, PackageExtra
import ckan.plugins.toolkit as toolkit

from rdkit import RDLogger

RDLogger.DisableLog("rdApp.error")

log = logging.getLogger(__name__)
_CANDIDATE_CACHE = None


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _mol_from_candidate(doc):
    smiles = (
        _first(doc.get("smiles"))
        or _first(doc.get("extras_smiles"))
    )

    inchi = (
        _first(doc.get("inchi"))
        or _first(doc.get("extras_inchi"))
    )

    if smiles:
        mol = Chem.MolFromSmiles(smiles)
        if mol is not None:
            return mol, smiles, "smiles"

    if inchi:
        mol = Chem.MolFromInchi(inchi)
        if mol is not None:
            return mol, inchi, "inchi"

    return None, None, None


def _canonical_smiles_from_query(smiles):
    mol = Chem.MolFromSmiles(smiles)

    if mol is None:
        raise toolkit.ValidationError({
            "smiles": ["Invalid SMILES. RDKit could not parse the query molecule."]
        })

    return Chem.MolToSmiles(mol, canonical=True), mol


def chemstructure_exact_search(context, data_dict):
    """
    Basic CKAN action for molecule structure search.

    Endpoint:
        /api/3/action/chemstructure_exact_search

    Input:
        {
          "smiles": "CCO",
          "mode": "exact",
          "rows": 50
        }

    Modes:
        exact  - canonical SMILES equality
        smarts - SMARTS substructure match
    """

    toolkit.check_access("package_search", context, data_dict)

    query = data_dict.get("smiles") or data_dict.get("query")
    mode = data_dict.get("mode", "exact")
    rows = int(data_dict.get("rows", 50))

    if not query:
        raise toolkit.ValidationError({
            "smiles": ["SMILES or SMARTS query is required."]
        })

    if mode not in ("exact", "smarts"):
        raise toolkit.ValidationError({
            "mode": ["Mode must be either 'exact' or 'smarts'."]
        })

    query_canon = None
    query_mol = None
    query_pattern = None

    if mode == "exact":
        query_canon, query_mol = _canonical_smiles_from_query(query)

    if mode == "smarts":
        query_pattern = Chem.MolFromSmarts(query)
        if query_pattern is None:
            raise toolkit.ValidationError({
                "smarts": ["Invalid SMARTS. RDKit could not parse the query pattern."]
            })

    search_data = {
        "q": "*:*",
        "fq": "+dataset_type:molecule",
        "fl": "id,name,title,smiles,extras_smiles,inchi,extras_inchi",
        "rows": 1000,
    }

    solr_result = toolkit.get_action("package_search")(context, search_data)
    candidates = solr_result.get("results", [])

    log.warning(
        "CHEMSTRUCTURE EXACT SEARCH mode=%s query=%s candidates=%s",
        mode,
        query,
        len(candidates),
    )

    hits = []

    for doc in candidates:
        mol, source_value, source_type = _mol_from_candidate(doc)

        if mol is None:
            continue

        matched = False
        candidate_canon = None

        if mode == "exact":
            candidate_canon = Chem.MolToSmiles(mol, canonical=True)
            matched = candidate_canon == query_canon

        elif mode == "smarts":
            matched = mol.HasSubstructMatch(query_pattern)
            candidate_canon = Chem.MolToSmiles(mol, canonical=True)

        if matched:
            hits.append({
                "id": doc.get("id"),
                "name": doc.get("name"),
                "title": doc.get("title"),
                "match_mode": mode,
                "structure_source": source_type,
                "structure_value": source_value,
                "canonical_smiles": candidate_canon,
            })

        if len(hits) >= rows:
            break

    return {
        "count": len(hits),
        "mode": mode,
        "query": query,
        "query_canonical_smiles": query_canon,
        "results": hits,
    }


def _make_morgan_fp(mol, radius=2, fp_size=2048):
    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=radius,
        fpSize=fp_size,
    )
    return generator.GetFingerprint(mol)


def _mol_from_smiles_or_inchi(smiles=None, inchi=None, package_name=None):
    if smiles:
        mol = Chem.MolFromSmiles(smiles)
        if mol is not None:
            return mol, smiles, "smiles"

        log.warning(
            "CHEMSTRUCTURE invalid SMILES package=%s smiles=%s",
            package_name,
            smiles,
        )

    if inchi:
        mol = Chem.MolFromInchi(inchi)
        if mol is not None:
            return mol, inchi, "inchi"

        log.warning(
            "CHEMSTRUCTURE invalid InChI package=%s inchi=%s",
            package_name,
            inchi,
        )

    return None, None, None


def _query_mol_from_input(query, mode):
    """
    Parse the user query according to search mode.

    exact/similarity/substructure:
        query is expected to be SMILES, normally exported from Ketcher.

    smarts:
        query is expected to be a SMARTS pattern.
        This can be kept for advanced/manual usage.
    """

    if mode == "smarts":
        pattern = Chem.MolFromSmarts(query)
        if pattern is None:
            raise toolkit.ValidationError({
                "smarts": ["Invalid SMARTS. RDKit could not parse the query."]
            })
        return pattern

    mol = Chem.MolFromSmiles(query)
    if mol is None:
        raise toolkit.ValidationError({
            "smiles": ["Invalid SMILES. RDKit could not parse the query."]
        })

    return mol


def _load_molecule_packages_from_db():
    """
    Load active CKAN molecule packages and their SMILES/InChI extras directly
    from PostgreSQL.

    This does not use Solr.
    """

    rows = (
        model.Session.query(
            Package.id,
            Package.name,
            Package.title,
            PackageExtra.key,
            PackageExtra.value,
        )
        .join(PackageExtra, Package.id == PackageExtra.package_id)
        .filter(Package.type == "molecule")
        .filter(Package.state == "active")
        .filter(PackageExtra.state == "active")
        .filter(PackageExtra.key.in_(["smiles", "inchi"]))
        .all()
    )

    molecules = {}

    for package_id, name, title, key, value in rows:
        item = molecules.setdefault(package_id, {
            "id": package_id,
            "name": name,
            "title": title,
            "smiles": None,
            "inchi": None,
        })

        if key == "smiles":
            item["smiles"] = value
        elif key == "inchi":
            item["inchi"] = value

    return list(molecules.values())

def _load_cached_structure_candidates(force_refresh=False):
    """
    Load and cache RDKit-ready molecule candidates.

    This avoids reparsing every molecule and regenerating fingerprints on every
    structure-search request.
    """

    global _CANDIDATE_CACHE

    if _CANDIDATE_CACHE is not None and not force_refresh:
        return _CANDIDATE_CACHE

    raw_candidates = _load_molecule_packages_from_db()
    cached_candidates = []

    for item in raw_candidates:
        mol, source_value, source_type = _mol_from_smiles_or_inchi(
            smiles=item.get("smiles"),
            inchi=item.get("inchi"),
            package_name=item.get("name"),
        )

        if mol is None:
            continue

        canonical_smiles = Chem.MolToSmiles(mol, canonical=True)
        fingerprint = _make_morgan_fp(mol)

        cached_candidates.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "title": item.get("title"),
            "mol": mol,
            "fingerprint": fingerprint,
            "canonical_smiles": canonical_smiles,
            "structure_source": source_type,
            "structure_value": source_value,
        })

    _CANDIDATE_CACHE = cached_candidates

    log.warning(
        "CHEMSTRUCTURE candidate cache built raw=%s cached=%s",
        len(raw_candidates),
        len(cached_candidates),
    )

    return _CANDIDATE_CACHE

def run_structure_search(query, mode="similarity", threshold=0.25, rows=None):
    """
    Reusable RDKit structure search.

    This is used by:
    - chemstructure_rdkit_search API action
    - /molecule page filtering via IPackageController.before_search

    """

    threshold = float(threshold)
    threshold = max(0.0, min(threshold, 1.0))

    if not query:
        raise toolkit.ValidationError({
            "query": ["SMILES or SMARTS query is required."]
        })

    if mode not in ("exact", "similarity","substructure" ,"smarts", ):
        raise toolkit.ValidationError({
            "mode": ["Mode must be one of: exact, smarts, similarity, substructure"]
        })

    query_obj = _query_mol_from_input(query, mode)

    query_canon = None
    query_fp = None

    if mode in ("exact", "similarity", "substructure"):
        query_canon = Chem.MolToSmiles(query_obj, canonical=True)

    if mode == "similarity":
        query_fp = _make_morgan_fp(query_obj)

    candidates = _load_cached_structure_candidates()

    log.warning(
        "CHEMSTRUCTURE STRUCTURE SEARCH mode=%s query=%s candidates=%s threshold=%s",
        mode,
        query,
        len(candidates),
        threshold,
    )

    hits = []

    for item in candidates:
        mol = item.get("mol")
        candidate_canon = item.get("canonical_smiles")

        matched = False
        similarity = None

        if mode == "exact":
            matched = candidate_canon == query_canon

        elif mode in ("substructure", "smarts"):
            matched = mol.HasSubstructMatch(query_obj)

        elif mode == "similarity":
            candidate_fp = item.get("fingerprint")
            similarity = DataStructs.TanimotoSimilarity(query_fp, candidate_fp)
            matched = similarity >= threshold

        if matched:
            result = {
                "id": item.get("id"),
                "name": item.get("name"),
                "title": item.get("title"),
                "mode": mode,
                "structure_source": item.get("structure_source"),
                "canonical_smiles": candidate_canon,
            }

            if similarity is not None:
                result["similarity"] = round(float(similarity), 4)

            hits.append(result)

            if rows is not None and len(hits) >= rows:
                break

    if mode == "similarity":
        hits.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    else:
        hits.sort(key=lambda x: x.get("name") or "")

    return {
        "count": len(hits),
        "query": query,
        "query_canonical_smiles": query_canon,
        "threshold": threshold if mode == "similarity" else None,
        "source": "postgresql_rdkit",
        "solr_used": False,
        "results": hits,
    }

def chemstructure_rdkit_search(context, data_dict):
    """
    Solr-independent RDKit structure search.

    Endpoint:
        /api/3/action/chemstructure_rdkit_search
    """

    toolkit.check_access("package_search", context, data_dict)

    query = data_dict.get("query") or data_dict.get("smiles")
    mode = data_dict.get("mode", "similarity")
    threshold = float(data_dict.get("threshold", 0.25))
    rows_limit = int(data_dict.get("rows", 50))

    result = run_structure_search(
        query=query,
        mode=mode,
        threshold=threshold,
        rows=rows_limit,
    )

    return result