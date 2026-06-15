import logging

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

from ckanext.chemstructure_search.action import (chemstructure_exact_search, chemstructure_rdkit_search)

from ckanext.chemstructure_search.views import get_blueprints


log = logging.getLogger(__name__)


class ChemstructureSearchPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IActions)

    def update_config(self, config):
        toolkit.add_template_directory(config, "templates")
        toolkit.add_public_directory(config, "public")

    def get_blueprint(self):
        return get_blueprints()

    def get_actions(self):
        return {
            "chemstructure_exact_search": chemstructure_exact_search,
            "chemstructure_rdkit_search": chemstructure_rdkit_search,
        }