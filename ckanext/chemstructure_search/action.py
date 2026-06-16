import base64
import logging

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

import ckan.model as model
from ckan.model import Package, PackageExtra
import ckan.plugins.toolkit as toolkit


log = logging.getLogger(__name__)


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
        "CHEMSTRUCTURE UI SEARCH mode=%s query=%s candidates=%s",
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


def _mol_from_smiles_or_inchi(smiles=None, inchi=None):
    if smiles:
        mol = Chem.MolFromSmiles(smiles)
        if mol is not None:
            return mol, smiles, "smiles"

    if inchi:
        mol = Chem.MolFromInchi(inchi)
        if mol is not None:
            return mol, inchi, "inchi"

    return None, None, None


def _query_mol_from_input(query, mode):
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


def chemstructure_rdkit_search(context, data_dict):
    """
    Solr-independent RDKit structure search.

    Endpoint:
        /api/3/action/chemstructure_rdkit_search

    Input examples:

        Exact:
        {
          "query": "CCO",
          "mode": "exact",
          "rows": 50
        }

        SMARTS:
        {
          "query": "[#6]-[#8]",
          "mode": "smarts",
          "rows": 50
        }

        Similarity:
        {
          "query": "CCO",
          "mode": "similarity",
          "threshold": 0.7,
          "rows": 50
        }
    """

    toolkit.check_access("package_search", context, data_dict)

    query = data_dict.get("query") or data_dict.get("smiles")
    mode = data_dict.get("mode", "similarity")
    threshold = float(data_dict.get("threshold", 0.25)) # increase similarity here. 25% similarity now
    threshold = max(0.0, min(threshold, 1.0)) # clamp for exact
    rows_limit = int(data_dict.get("rows", 50))

    if not query:
        raise toolkit.ValidationError({
            "query": ["SMILES or SMARTS query is required."]
        })

    if mode not in ("exact", "smarts", "similarity"):
        raise toolkit.ValidationError({
            "mode": ["Mode must be one of: exact, smarts, similarity."]
        })

    query_obj = _query_mol_from_input(query, mode)

    query_canon = None
    query_fp = None

    if mode in ("exact", "similarity"):
        query_canon = Chem.MolToSmiles(query_obj, canonical=True)
        query_fp = _make_morgan_fp(query_obj)

    candidates = _load_molecule_packages_from_db()

    log.warning(
        "CHEMSTRUCTURE RDKIT SEARCH mode=%s query=%s candidates=%s threshold=%s",
        mode,
        query,
        len(candidates),
        threshold,
    )

    hits = []

    for item in candidates:
        mol, source_value, source_type = _mol_from_smiles_or_inchi(
            smiles=item.get("smiles"),
            inchi=item.get("inchi"),
        )

        if mol is None:
            continue

        candidate_canon = Chem.MolToSmiles(mol, canonical=True)

        matched = False
        similarity = None

        if mode == "exact":
            matched = candidate_canon == query_canon

        elif mode == "smarts":
            matched = mol.HasSubstructMatch(query_obj)

        elif mode == "similarity":
            candidate_fp = _make_morgan_fp(mol)
            similarity = DataStructs.TanimotoSimilarity(query_fp, candidate_fp)
            matched = similarity >= threshold

        if mode == "similarity":
            hits.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        else:
            hits.sort(key=lambda x: x.get("name") or "")

        if matched:
            result = {
                "id": item.get("id"),
                "name": item.get("name"),
                "title": item.get("title"),
                "mode": mode,
                "structure_source": source_type,
                "canonical_smiles": candidate_canon,
            }

            if similarity is not None:
                result["similarity"] = round(float(similarity), 4)

            hits.append(result)

    if mode == "similarity":
        hits.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    else:
        hits.sort(key=lambda x: x.get("name") or "")

    return {
        "count": len(hits),
        #"mode": mode,
        "query": query,
        "query_canonical_smiles": query_canon,
        "threshold": threshold if mode == "similarity" else None,
        "source": "postgresql_rdkit",
        "solr_used": False,
        "results": hits[:rows_limit],
    }