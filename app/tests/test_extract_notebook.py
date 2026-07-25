"""Tests for /extract_notebook dependency extraction.

Covers the robustness fixes that raise the extraction success rate on
real-world notebooks:
  - IPython magics / shell escapes no longer break parsing,
  - a single unparseable cell no longer zeroes the whole notebook,
  - import extraction is independent of pytype,
  - relative/local imports and stdlib are excluded,
  - unmapped third-party packages route to pip, mapped ones to conda.

The extraction and routing logic is unit-tested directly (no auth/network),
plus one endpoint test with auth and the module mapping stubbed out.
"""
import nbformat
import yaml

from app.models.workflow_cell import Cell
from app.models.notebook_data import NotebookData
from app.services.cell_extractor.py_extractor import (
    extract_notebook_imports, scrub_ipython_magics, PyExtractor)
from app.services.containerizers.py_containerizer import PyContainerizer

MODULE_MAPPING = {
    'conda': {'sklearn': 'scikit-learn', 'cv2': 'opencv', 'torch': 'pytorch'},
    'pip': {'yaml': 'pyyaml'},
    'r': {},
}


def _notebook(*cell_sources):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(s) for s in cell_sources]
    return nb


def _import_names(notebook):
    return {(d.get('module') or d.get('name')).split('.')[0]
            for d in extract_notebook_imports(notebook)}


# --- scrub_ipython_magics -------------------------------------------------

def test_scrub_line_magic_and_shell():
    src = ('import numpy as np\n%matplotlib inline\n'
           '!pip install foo\nx = np.zeros(3)')
    scrubbed = scrub_ipython_magics(src)
    assert '%matplotlib' not in scrubbed
    assert '!pip' not in scrubbed
    assert 'import numpy as np' in scrubbed
    assert 'x = np.zeros(3)' in scrubbed
    # line count preserved so error line numbers stay meaningful
    assert len(scrubbed.splitlines()) == len(src.splitlines())


def test_scrub_cell_magic_blanks_rest_of_cell():
    src = '%%bash\nfor i in 1 2 3; do echo $i; done'
    scrubbed = scrub_ipython_magics(src)
    assert scrubbed.strip() == ''


def test_scrub_help_syntax():
    assert scrub_ipython_magics('numpy.array?').strip() == ''


# --- extract_notebook_imports --------------------------------------------

def test_imports_survive_magics():
    nb = _notebook(
        'import pandas as pd\n%matplotlib inline',
        'import neurokit2 as nk\n%timeit nk.ecg_simulate()',
    )
    # Real imports survive the magics, and %matplotlib additionally implies
    # matplotlib (which the notebook needs but never `import`s). %timeit is not
    # a package magic and contributes nothing.
    assert _import_names(nb) == {'pandas', 'neurokit2', 'matplotlib'}


def test_one_bad_cell_does_not_zero_the_notebook():
    # A malformed cell (a stray magic with no % marker) must not cost the
    # imports found in the other cells.
    nb = _notebook(
        'import jax\nimport numpy as np',
        'timeit -n 5 foo(bar)',  # invalid Python, not a recognizable magic
    )
    assert _import_names(nb) == {'jax', 'numpy'}


def test_load_ext_magic_yields_dependency():
    # %load_ext watermark needs the `watermark` package, though it is never
    # imported with an `import` statement.
    nb = _notebook('%load_ext watermark\n%watermark -v')
    assert 'watermark' in _import_names(nb)


def test_pylab_magic_implies_matplotlib_and_numpy():
    nb = _notebook('%pylab inline\nx = arange(10)')
    assert {'matplotlib', 'numpy'} <= _import_names(nb)


def test_pip_install_magic_yields_targets():
    nb = _notebook('!pip install seaborn plotly==5.1\n'
                   '%pip install -U statsmodels\n'
                   'import seaborn as sns')
    names = _import_names(nb)
    assert {'seaborn', 'plotly', 'statsmodels'} <= names
    # flags and version specifiers are stripped, not captured as packages
    assert '-U' not in names
    assert 'plotly==5.1' not in names


def test_from_imports_use_module_name():
    nb = _notebook('from sklearn.model_selection import train_test_split')
    deps = extract_notebook_imports(nb)
    assert any(d['module'] == 'sklearn.model_selection' for d in deps)


def test_relative_imports_excluded():
    nb = _notebook('from . import helpers\nfrom ..pkg import thing\n'
                   'import requests')
    assert _import_names(nb) == {'requests'}


def test_extraction_is_pytype_independent():
    # extract_notebook_imports uses a plain AST walk; it must not depend on
    # pytype succeeding. Constructing a full PyExtractor (which does invoke
    # pytype) must also still yield imports thanks to the fallback.
    nb = _notebook('import scanpy\nx: int = 1\ny = x + 1')
    assert 'scanpy' in _import_names(nb)
    ext = PyExtractor(NotebookData(cell_index=0, kernel='ipython',
                                   notebook=nb, user_name='u'),
                      base_image_tags_url='')
    assert 'scanpy' in {v['name'].split('.')[0]
                        for v in ext.notebook_imports.values()}


# --- map_dependencies (conda vs pip routing) ------------------------------

def _route(notebook):
    deps = extract_notebook_imports(notebook)
    cell = Cell(title='notebook', base_container_image={}, dependencies=deps,
                kernel='ipython', original_source='')
    routed = PyContainerizer(cell).map_dependencies(deps, MODULE_MAPPING)
    return (sorted(routed['conda_dependencies']),
            sorted(routed['pip_dependencies']))


def test_unmapped_package_routes_to_pip():
    conda, pip = _route(_notebook('import neurokit2'))
    assert 'neurokit2' in pip
    assert 'neurokit2' not in conda


def test_mapped_package_routes_to_conda_with_renamed_name():
    conda, pip = _route(_notebook('import sklearn'))
    assert 'scikit-learn' in conda


def test_stdlib_is_dropped():
    conda, pip = _route(
        _notebook('import os\nimport sys\nimport json\nimport requests'))
    assert conda == []
    assert pip == ['requests']


def test_pip_mapping_entry_routes_to_pip_with_renamed_name():
    conda, pip = _route(_notebook('import yaml'))
    assert 'pyyaml' in pip


def test_pip_name_fallback_renames_unmapped_import():
    # `bs4` is absent from the module mapping stub, but the local fallback
    # must still emit the correct PyPI name rather than the unresolvable
    # import name.
    conda, pip = _route(_notebook('import bs4'))
    assert 'beautifulsoup4' in pip
    assert 'bs4' not in pip


# --- build_environment (template render, no R/network) --------------------

def test_build_environment_pins_python_and_renders_deps(monkeypatch):
    import app.services.containerizers.containerizer as containerizer_module
    monkeypatch.setattr(containerizer_module, 'get_module_name_mapping',
                        lambda url: MODULE_MAPPING)
    nb = _notebook('import yaml\n%matplotlib inline')
    deps = extract_notebook_imports(nb)
    cell = Cell(title='notebook', base_container_image={}, dependencies=deps,
                kernel='ipython', original_source='')
    env_text = PyContainerizer(cell).build_environment()
    env = yaml.safe_load(env_text)
    conda = env['dependencies']
    pip = next(
        (d['pip'] for d in conda if isinstance(d, dict) and 'pip' in d), [])
    assert 'python=3.11' in conda      # Python pinned in the generated spec
    assert 'pyyaml' in pip             # yaml -> pyyaml (mapping)
    assert 'matplotlib' in pip         # %matplotlib magic -> matplotlib


# --- endpoint (auth + module mapping stubbed) -----------------------------

def test_extract_notebook_endpoint_yaml(monkeypatch):
    import pytest
    from fastapi.testclient import TestClient
    try:
        import app.main as main_module
    except Exception as e:
        # app.main imports rpy2, which needs an R installation on PATH/R_HOME.
        # The Python extraction path under test doesn't use R; skip the
        # endpoint-level assertion where R is unavailable (the extraction and
        # routing logic is fully covered by the unit tests above).
        pytest.skip(f'app.main import requires R: {e}')
    import app.services.containerizers.containerizer as containerizer_module

    main_module.app.dependency_overrides[main_module.valid_access_token] = (
        lambda: {'preferred_username': 'tester'})
    monkeypatch.setattr(
        main_module.settings, 'get_vl_config',
        lambda vl: type('VL', (), {'module_mapping_url': 'stub'})())
    monkeypatch.setattr(
        containerizer_module, 'get_module_name_mapping',
        lambda url: MODULE_MAPPING)

    nb = _notebook('import neurokit2 as nk\n%matplotlib inline',
                   'from sklearn.model_selection import train_test_split')
    payload = {'virtual_lab': 'test-virtual-lab-1',
               'data': {'kernel': 'ipython', 'notebook': nb}}
    try:
        response = TestClient(main_module.app).post(
            '/extract_notebook',
            headers={'Authorization': 'Bearer x'}, json=payload)
    finally:
        main_module.app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    env = yaml.safe_load(response.text)
    conda = env.get('dependencies', [])
    pip = next(
        (d['pip'] for d in conda if isinstance(d, dict) and 'pip' in d), [])
    assert 'python=3.11' in conda       # Python is pinned
    assert 'scikit-learn' in conda      # mapped -> conda
    assert 'neurokit2' in pip           # unmapped -> pip
    assert 'matplotlib' in pip          # %matplotlib magic -> matplotlib
