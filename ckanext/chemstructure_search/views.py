import ckan.plugins.toolkit as toolkit
from flask import Blueprint


def structure_search_page():
    return toolkit.render("chemstructure_search/search.html")


def get_blueprints():
    blueprint = Blueprint(
        "chemstructure_search",
        __name__,
        url_prefix="/chemstructure-search",
    )

    blueprint.add_url_rule(
        "/",
        view_func=structure_search_page,
        methods=["GET"],
    )

    return [blueprint]