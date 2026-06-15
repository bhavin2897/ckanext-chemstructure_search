import base64
import logging

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

import ckan.plugins.toolkit as toolkit
from ckan.lib.search import SearchError


log = logging.getLogger(__name__)


def _first(value):
    """
    CKAN/Solr may return some fields either as a scalar value
    or as a list. For similarity search we only need the first value.
    """
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _base64_to_fp(fp_b64):
    """
    Decode a base64-encoded RDKit ExplicitBitVect.

    The fingerprint was originally encoded using:
        DataStructs.BitVectToBinaryText(fp)
        base64.b64encode(...)
    """
    if not fp_b64:
        return None

    fp_b64 = _first(fp_b64)

    binary = base64.b64decode(fp_b64)
    return DataStructs.cDataStructs.CreateFromBinaryText(binary)


def _query_fp_from_smiles(smiles):
    """
    Convert query SMILES into RDKit Morgan fingerprint.
    """
    if not smiles:
        raise toolkit.ValidationError({
            "smiles": ["SMILES is required."]
        })

    mol = Chem.MolFromSmiles(smiles)

    if mol is None:
        raise toolkit.ValidationError({
            "smiles": ["Invalid SMILES. RDKit could not parse the query structure."]
        })

    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=2,
        fpSize=2048,
    )

    return generator.GetFingerprint(mol)


def _get_candidate_fp(doc):
    """
    CKAN package_search strips the extras_ prefix in API results.

    Solr field:
        extras_fp_morgan_2048_b64

    CKAN package_search result field:
        fp_morgan_2048_b64

    We support both to be safe.
    """
    return _first(
        doc.get("fp_morgan_2048_b64")
        or doc.get("extras_fp_morgan_2048_b64")
    )


def _get_candidate_smiles(doc):
    return _first(
        doc.get("smiles_canon")
        or doc.get("extras_smiles_canon")
    )


def _get_candidate_popcnt(doc):
    return _first(
        doc.get("fp_morgan_2048_popcnt")
        or doc.get("extras_fp_morgan_2048_popcnt")
    )


def chemstructure_similarity_search(context, data_dict):
    """
    CKAN action:
        /api/3/action/chemstructure_similarity_search

    Input:
        {
          "smiles": "CCO",
          "threshold": 0.7,
          "rows": 10
        }

    Behavior:
        1. Generate RDKit fingerprint for query SMILES
        2. Fetch molecule candidates from Solr via package_search
        3. Decode stored fingerprints
        4. Compute Tanimoto similarity
        5. Return ranked results
    """
    toolkit.check_access("package_search", context, data_dict)

    smiles = data_dict.get("smiles")
    threshold = float(data_dict.get("threshold", 0.7))
    rows = int(data_dict.get("rows", 10))

    query_fp = _query_fp_from_smiles(smiles)

    search_data = {
        "q": "*:*",
        "fq": '+dataset_type:molecule +extras_is_structure_searchable:"true"',
        "fl": (
            "name,title,"
            "extras_smiles_canon,"
            "extras_fp_morgan_2048_b64,"
            "extras_fp_morgan_2048_popcnt"
        ),
        "rows": 1000,
    }

    try:
        solr_result = toolkit.get_action("package_search")(context, search_data)
    except SearchError as e:
        log.exception("Solr search failed during chemstructure similarity search")
        raise toolkit.ValidationError({
            "solr": [str(e)]
        })

    results = solr_result.get("results", [])

    log.warning(
        "CHEMSTRUCTURE SIM package_search count=%s results_len=%s",
        solr_result.get("count"),
        len(results),
    )

    for doc in results[:10]:
        log.warning(
            "CHEMSTRUCTURE SIM candidate name=%s keys=%s smiles=%s fp_present=%s popcnt=%s",
            doc.get("name"),
            sorted(doc.keys()),
            _get_candidate_smiles(doc),
            bool(_get_candidate_fp(doc)),
            _get_candidate_popcnt(doc),
        )

    hits = []

    for doc in results:
        candidate_name = doc.get("name")
        fp_b64 = _get_candidate_fp(doc)

        if not fp_b64:
            log.warning(
                "CHEMSTRUCTURE SIM skipping candidate without fingerprint name=%s keys=%s",
                candidate_name,
                sorted(doc.keys()),
            )
            continue

        try:
            target_fp = _base64_to_fp(fp_b64)

            if target_fp is None:
                log.warning(
                    "CHEMSTRUCTURE SIM skipping candidate with empty decoded fingerprint name=%s",
                    candidate_name,
                )
                continue

            similarity = DataStructs.TanimotoSimilarity(query_fp, target_fp)

        except Exception as e:
            log.exception(
                "CHEMSTRUCTURE SIM failed to compute similarity candidate=%s error=%s",
                candidate_name,
                e,
            )
            continue

        log.warning(
            "CHEMSTRUCTURE SIM score candidate=%s similarity=%s threshold=%s",
            candidate_name,
            similarity,
            threshold,
        )

        if similarity >= threshold:
            hits.append({
                "name": candidate_name,
                "title": doc.get("title"),
                "smiles_canon": _get_candidate_smiles(doc),
                "similarity": round(float(similarity), 4),
                "fp_morgan_2048_popcnt": _get_candidate_popcnt(doc),
            })

    hits.sort(key=lambda item: item["similarity"], reverse=True)

    return {
        "count": len(hits),
        "threshold": threshold,
        "query_smiles": smiles,
        "results": hits[:rows],
    }