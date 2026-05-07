import copy
import json
import os

from fastapi.testclient import TestClient

from app.main import app
from app.models.notebook_dependencies import NotebookDependencies

if os.path.exists('resources'):
    base_path = 'resources'
elif os.path.exists('app/tests/resources/'):
    base_path = 'app/tests/resources/'
client = TestClient(app)


def _load_notebook_payload(cell_dir_name):
    """Load a notebook and its extract payload from a test resource directory."""
    cell_dir = os.path.join(base_path, 'notebook_cells', cell_dir_name)
    notebook_path = os.path.join(cell_dir, 'notebook.ipynb')
    with open(notebook_path) as f:
        notebook = json.load(f)
    payload_path = os.path.join(cell_dir, 'payload_extract_cell.json')
    with open(payload_path) as f:
        cell_payload = json.load(f)
    # Build a NotebookExtractorPayload: same virtual_lab and kernel, but no
    # cell_index — the endpoint iterates over all cells itself.
    payload = {
        'virtual_lab': cell_payload['virtual_lab'],
        'data': {
            'kernel': cell_payload['data']['kernel'],
            'notebook': notebook,
        },
    }
    return payload


def test_extract_notebook_returns_200():
    """Endpoint returns 200 for a valid notebook."""
    payload = _load_notebook_payload('create-file-user')
    auth_token = os.getenv('AUTH_TOKEN')
    response = client.post(
        '/extract_notebook/',
        headers={'Authorization': 'Bearer ' + auth_token},
        json=payload,
    )
    assert response.status_code == 200, response.text


def test_extract_notebook_returns_dependencies():
    """Dependencies are extracted and deduplicated across all code cells."""
    payload = _load_notebook_payload('create-file-user')
    auth_token = os.getenv('AUTH_TOKEN')
    response = client.post(
        '/extract_notebook/',
        headers={'Authorization': 'Bearer ' + auth_token},
        json=payload,
    )
    assert response.status_code == 200, response.text
    result = NotebookDependencies.model_validate(response.json())
    assert result.dependencies is not None
    # The create-file-user notebook imports os — verify it is present
    dep_names = [d['name'] for d in result.dependencies]
    assert 'os' in dep_names, (
        f"Expected 'os' in dependencies, got: {dep_names}"
    )
    # Deduplication: no two entries share the same (name, asname, module) tuple
    keys = [
        (d.get('name'), d.get('asname'), d.get('module'))
        for d in result.dependencies
    ]
    assert len(keys) == len(set(keys)), (
        f"Duplicate dependencies found: {result.dependencies}"
    )


def test_extract_notebook_skips_non_code_cells():
    """Non-code (markdown/raw) cells are skipped without error."""
    payload = _load_notebook_payload('create-file-user')
    # Inject a markdown cell at the start of the notebook
    markdown_cell = {
        'cell_type': 'markdown',
        'metadata': {},
        'source': '# This is a markdown cell',
    }
    payload['data']['notebook']['cells'].insert(0, markdown_cell)
    auth_token = os.getenv('AUTH_TOKEN')
    response = client.post(
        '/extract_notebook/',
        headers={'Authorization': 'Bearer ' + auth_token},
        json=payload,
    )
    assert response.status_code == 200, response.text
    result = NotebookDependencies.model_validate(response.json())
    assert result.dependencies is not None


def test_extract_notebook_partial_results_on_cell_failure():
    """If one cell fails extraction, others still contribute results."""
    payload = _load_notebook_payload('create-file-user')
    # Inject a malformed code cell that will fail extraction
    broken_cell = {
        'cell_type': 'code',
        'metadata': {},
        'outputs': [],
        'execution_count': None,
        'source': '{{{{invalid python source}}}}',
    }
    original_cells = payload['data']['notebook']['cells']
    payload['data']['notebook']['cells'] = [broken_cell] + original_cells
    auth_token = os.getenv('AUTH_TOKEN')
    response = client.post(
        '/extract_notebook/',
        headers={'Authorization': 'Bearer ' + auth_token},
        json=payload,
    )
    # Should still succeed with partial results from the valid cells
    assert response.status_code == 200, response.text
    result = NotebookDependencies.model_validate(response.json())
    dep_names = [d['name'] for d in (result.dependencies or [])]
    assert 'os' in dep_names, (
        f"Expected partial results with 'os', got: {dep_names}"
    )
