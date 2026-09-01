import logging

from flask import request

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit


def chemstructure_ketcher_url():
    """Return the embedded Ketcher URL with deployment-specific controls."""

    ketcher_url = "/chemstructure_search/ketcher/index.html"
    show_text = toolkit.asbool(toolkit.config.get(
        "ckanext.chemstructure_search.show_ketcher_text",
        False,
    ))

    if show_text:
        return "{}?showText=true".format(ketcher_url)

    return ketcher_url


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
