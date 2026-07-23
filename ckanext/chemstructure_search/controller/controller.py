import ckan.plugins.toolkit as toolkit

from ckanext.chemstructure_search.action import run_structure_search


def chemstructure_similarity_search(context, data_dict):
    """
    Compatibility action for the previous similarity-search controller.

    Stored molecule matching is delegated to the PostgreSQL RDKit cartridge
    implementation in action.run_structure_search().
    """

    toolkit.check_access("package_search", context, data_dict)

    query = data_dict.get("query") or data_dict.get("smiles")

    return run_structure_search(
        query=query,
        mode="similarity",
        threshold=data_dict.get("threshold", 0.7),
        rows=data_dict.get("rows", 10),
    )
