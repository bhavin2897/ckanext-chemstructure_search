import logging

from flask import request

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit


def chemstructure_show_ketcher_text():
    """Return whether the synchronized query text field should be visible."""

    return toolkit.asbool(toolkit.config.get(
        "ckanext.chemstructure_search.show_ketcher_text",
        False,
    ))


def chemstructure_search_params():
    """
    Return active structure-search URL parameters for templates.
    """

    try:
        structure_query = request.args.get("structure_query")
    except RuntimeError:
        return {}

    if not structure_query:
        return {}

    structure_mode = request.args.get("structure_mode", "similarity")
    threshold = request.args.get("threshold", "0.25")

    mode_labels = {
        "exact": "Exact match",
        "similarity": "Fingerprint similarity",
        "substructure": "Substructure",
        "smarts": "SMARTS pattern",
    }

    clear_url = "/molecule?sort=title_string+asc"

    return {
        "query": structure_query,
        "mode": structure_mode,
        "mode_label": mode_labels.get(structure_mode, structure_mode),
        "threshold": threshold,
        "clear_url": clear_url,
    }
