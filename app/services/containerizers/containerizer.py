from abc import abstractmethod

import cachetools.func
import requests
from jinja2 import Environment, PackageLoader

from app.models.workflow_cell import Cell


@cachetools.func.ttl_cache(ttl=6 * 3600)
def get_module_name_mapping(module_mapping_url: str = None):
    try:
        return requests.get(module_mapping_url).json()
    except Exception as e:
        raise ValueError('Failed to load module mapping from ' +
                         module_mapping_url) from e


class Containerizer():

    def __init__(self, cell: Cell, module_mapping_url=None):
        self.cell = cell
        loader = PackageLoader('app', 'templates')
        self.template_env = Environment(loader=loader, trim_blocks=True,
                                        lstrip_blocks=True)
        self.visualization_cell = False
        if self.cell.title.lower().startswith('visualize-'):
            self.visualization_cell = True
        self.file_extension = ''
        self.template_script = ''
        self.template_conda_env = 'conda_env_template.jinja2'
        self.dockerfile_template = 'dockerfile_template_conda.jinja2'
        self.module_mapping_url = module_mapping_url

    @abstractmethod
    def build_script(self):
        pass

    @abstractmethod
    def extract_notebook(self):
        pass

    def build_environment(self):
        template_conda = self.template_env.get_template(
            self.template_conda_env)
        mapped_dependencies = self.map_dependencies(
            dependencies=self.cell.dependencies,
            module_name_mapping=get_module_name_mapping
            (self.module_mapping_url)
        )
        return template_conda.render(conda_deps=list(
                                         mapped_dependencies[
                                             'conda_dependencies']),
                                     pip_deps=list(mapped_dependencies[
                                                       'pip_dependencies']))

    @abstractmethod
    def is_standard_module(self, module_name):
        pass

    def build_docker(self, environment_url=None):
        template_dockerfile = self.template_env.get_template(
            self.dockerfile_template)
        return template_dockerfile.render(
            title=self.cell.title,
            base_image=self.cell.base_container_image,
            environment_url=environment_url)

    @abstractmethod
    def map_dependencies(self, dependencies=None, module_name_mapping=None):
        pass
