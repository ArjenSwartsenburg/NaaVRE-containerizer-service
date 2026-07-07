import json
import sys
from abc import ABC

import nbformat as nbf

from app.models.workflow_cell import Cell
from app.services.containerizers.containerizer import Containerizer


# Import name → PyPI package name, for the common cases where they differ.
# Used only as a fallback when the (remote) module mapping doesn't cover the
# import: without it these route to pip under their import name and fail the
# install outright (e.g. a pip dependency literally named "yaml" — the package
# is "pyyaml"). The remote mapping still takes precedence.
_PIP_NAME_FALLBACKS = {
    'yaml': 'pyyaml',
    'cv2': 'opencv-python',
    'sklearn': 'scikit-learn',
    'skimage': 'scikit-image',
    'PIL': 'pillow',
    'bs4': 'beautifulsoup4',
    'Bio': 'biopython',
    'dateutil': 'python-dateutil',
    'OpenSSL': 'pyopenssl',
    'attr': 'attrs',
    'yaml_include': 'pyyaml-include',
}


class PyContainerizer(Containerizer, ABC):

    def __init__(self, cell: Cell, module_mapping_url=None):
        super().__init__(cell, module_mapping_url)
        self.file_extension = '.py'
        if self.visualization_cell:
            self.template_script = 'vis_cell_template.jinja2'
        else:
            self.template_script = 'py_cell_template.jinja2'

    def extract_notebook(self):
        # Build a notebook from the cell
        nb = nbf.v4.new_notebook()
        cells = [nbf.v4.new_code_cell(self.cell.original_source)]
        nb.cells.extend(cells)
        return json.dumps(nb, indent=2)

    def build_environment(self, python_version: str = '3.11'):
        # Pin Python for Python cells: an unconstrained spec resolves to the
        # newest interpreter, for which much of the corpus's older/C-extension
        # dependencies have no wheels and fail to build from source.
        return super().build_environment(python_version=python_version)

    def is_standard_module(self, module_name=None):
        # sys.stdlib_module_names lists the standard library by name, with no
        # need to import the candidate module (importing had side effects and
        # misclassified anything not installed in the service's own env).
        return (module_name in sys.stdlib_module_names
                or module_name in sys.builtin_module_names)

    def map_dependencies(self, dependencies=None, module_name_mapping=None):
        conda_deps = set()
        pip_deps = set()
        for dep in dependencies:
            module_name = dep.get('module')
            if not module_name:
                module_name = dep.get('name')
            if not module_name:
                continue
            module_name = module_name.split('.')[
                0] if '.' in module_name else module_name
            if module_name in module_name_mapping['conda']:
                conda_deps.add(module_name_mapping['conda'][module_name])
            elif module_name in module_name_mapping['pip']:
                pip_deps.add(module_name_mapping['pip'][module_name])
            elif not self.is_standard_module(module_name):
                # Unmapped third-party packages default to pip: arbitrary
                # notebook imports come from PyPI far more often than from
                # conda channels, and a name unknown to conda fails the
                # whole environment solve, whereas a bad pip package fails
                # in isolation. Conda-preferred packages belong in the
                # module mapping's "conda" section. A small fallback fixes the
                # common import-vs-package name mismatches (yaml→pyyaml, …).
                pip_deps.add(_PIP_NAME_FALLBACKS.get(module_name, module_name))
        conda_deps.discard(None)
        pip_deps.discard(None)
        return {'conda_dependencies': conda_deps, 'pip_dependencies': pip_deps}

    def build_script(self):
        template_script = self.template_env.get_template(self.template_script)
        deps = self.cell.dependencies
        conf = self.cell.confs
        resolves = []
        for d in deps:
            resolve_to = "import %s" % d['name']
            if d['module']:
                resolve_to = "from %s %s" % (d['module'], resolve_to)
            if d['asname']:
                resolve_to += " as %s" % d['asname']
            resolves.append(resolve_to)
        return template_script.render(cell=self.cell,
                                      deps=resolves,
                                      confs=conf)
