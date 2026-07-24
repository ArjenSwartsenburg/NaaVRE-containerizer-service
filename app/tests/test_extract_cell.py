import json
import os
from urllib.parse import quote

import requests
from fastapi.testclient import TestClient
from nbformat import v4, write

from app.main import app
from app.models.workflow_cell import Cell

if os.path.exists('resources'):
    base_path = 'resources'
elif os.path.exists('app/tests/resources/'):
    base_path = 'app/tests/resources/'
client = TestClient(app)


def save_as_jupyter_notebook(notebook_data, notebook_filename):
    # Convert to Jupyter notebook format
    notebook = v4.new_notebook()
    notebook.cells = [
        v4.new_code_cell(cell['source']) for cell in notebook_data['cells']
    ]
    # Save as a Jupyter notebook file
    output_path = '/tmp/' + notebook_filename
    with open(output_path, 'w') as f:
        write(notebook, f)


def test_extract_cell():
    notebook_cells_dir = os.path.join(base_path, 'notebook_cells')
    cells_dirs = [f.path for f in os.scandir(notebook_cells_dir) if f.is_dir()]
    for cell_dir in cells_dirs:
        if 'r-153_function_as_var' not in cell_dir:
            continue
        notebook_path = os.path.join(cell_dir, 'notebook.ipynb')
        with open(notebook_path) as f:
            print('Testing extract for notebook: ' + notebook_path)
            notebook = json.load(f)
        payload_path = os.path.join(cell_dir, 'payload_extract_cell.json')
        with open(payload_path) as f:
            print('                with payload: ' + payload_path)
            extractor_json_payload = json.load(f)

        cell_name = os.path.basename(cell_dir)
        save_as_jupyter_notebook(notebook, f'{cell_name}.ipynb')

        extractor_json_payload['data']['notebook'] = notebook
        auth_token = os.getenv('AUTH_TOKEN')
        # Check if responses.json exists
        responses_dict = {'extract_cell': {'code': 200, 'message': 'OK'}}
        if os.path.exists(os.path.join(cell_dir, 'responses.json')):
            with open(os.path.join(cell_dir, 'responses.json')) as f:
                responses_dict = json.load(f)

        cell_extractor_response = client.post(
            '/extract_cell/',
            headers={'Authorization': 'Bearer ' + auth_token},
            json=extractor_json_payload,
        )
        if cell_extractor_response.status_code != \
                responses_dict['extract_cell']['code']:
            print(cell_extractor_response.text)
        assert cell_extractor_response.status_code == \
               responses_dict['extract_cell']['code']
        if cell_extractor_response.status_code != 200 and \
                responses_dict['extract_cell']['code'] != 200:
            continue
        cell_dict = cell_extractor_response.json()
        returned_cell = Cell.model_validate(cell_dict)
        cell_path = os.path.join(cell_dir, 'cell.json')
        with open(cell_path) as f:
            print('              expecting cell: ' + cell_path)
            expected_cell_dict = json.load(f)
        expected_cell = Cell.model_validate(expected_cell_dict)

        returned_cell_confs = sorted(returned_cell.confs,
                                     key=lambda x: x['name'])
        expected_cell_confs = sorted(expected_cell.confs,
                                     key=lambda x: x['name'])
        assert (len(returned_cell_confs) == len(expected_cell_confs)), \
            f"Expected {expected_cell_confs}, " \
            f"but got {returned_cell_confs}"

        assert expected_cell.title == returned_cell.title
        assert expected_cell.kernel == returned_cell.kernel

        returned_cell_inputs = sorted(returned_cell.inputs,
                                      key=lambda x: x['name'])
        expected_cell_inputs = sorted(expected_cell.inputs,
                                      key=lambda x: x['name'])

        assert (len(returned_cell_inputs) == len(expected_cell_inputs)), \
            f"Expected {expected_cell_inputs}, " \
            f"but got {returned_cell_inputs}"

        for returned, expected in zip(returned_cell_inputs,
                                      expected_cell_inputs):
            assert returned['name'] == expected['name']
            assert returned['type'] == expected['type']

        returned_cell_outputs = sorted(returned_cell.outputs,
                                       key=lambda x: x['name'])
        expected_cell_outputs = sorted(expected_cell.outputs,
                                       key=lambda x: x['name'])

        assert (len(returned_cell_outputs) == len(expected_cell_outputs)), \
            f"Expected {expected_cell_outputs}, " \
            f"but got {returned_cell_outputs}"

        for returned, expected in zip(returned_cell_outputs,
                                      expected_cell_outputs):
            assert expected['name'] == returned['name']
            assert expected['type'] == returned['type']

        returned_cell_params = sorted(returned_cell.params,
                                      key=lambda x: x['name'])
        expected_cell_params = sorted(expected_cell.params,
                                      key=lambda x: x['name'])

        assert (len(returned_cell_params) == len(expected_cell_params)), \
            f"Expected {expected_cell_outputs}, " \
            f"but got {returned_cell_params}"

        for returned, expected in zip(returned_cell_params,
                                      expected_cell_params):
            assert expected['name'] == returned['name']
            assert expected['type'] == returned['type']
            assert expected['default_value'] == returned['default_value']

        returned_cell_secrets = sorted(returned_cell.secrets,
                                       key=lambda x: x['name'])
        expected_cell_secrets = sorted(expected_cell.secrets,
                                       key=lambda x: x['name'])
        assert (len(returned_cell_secrets) == len(expected_cell_secrets)), \
            f"Expected {expected_cell_secrets}, " \
            f"but got {returned_cell_secrets}"

        for returned, expected in zip(returned_cell_secrets,
                                      expected_cell_secrets):
            assert returned['name'] == expected['name']
            assert returned['type'] == expected['type']

        returned_cell_deps = sorted(returned_cell.dependencies,
                                    key=lambda x: x['name'])
        expected_cell_dependencies = sorted(expected_cell.dependencies,
                                            key=lambda x: x['name'])
        assert (len(expected_cell_dependencies) == len(returned_cell_deps)), \
            f"Expected {expected_cell_dependencies}, " \
            f"but got {returned_cell_deps}"

        for returned, expected in zip(returned_cell_deps,
                                      expected_cell_dependencies):
            assert returned['name'] == expected['name']
            assert returned['asname'] == expected['asname']
            assert returned['module'] == expected['module']

        assert os.getenv('REGISTRY_TOKEN_FOR_TESTS') is not None, \
            "REGISTRY_TOKEN_FOR_TESTS is not set. "
        tag = get_container_tag_from_ghcr_url(
            expected_cell.base_container_image['build'],
            os.getenv('REGISTRY_TOKEN_FOR_TESTS'))

        # Check if the tag are not None. If None print the name of the image
        assert tag is not None, \
            (f"Failed to fetch tag for "
             f"{expected_cell.base_container_image['build']}")

        expected_cell.base_container_image['build'] = \
            (expected_cell.base_container_image['build'].rsplit(":", 1)[0] +
             f":{tag}")
        expected_cell.base_container_image['runtime'] = \
            (expected_cell.base_container_image['runtime'].rsplit(":", 1)[0] +
             f":{tag}")

        assert (returned_cell.base_container_image == expected_cell.
                base_container_image)
        # In R we add libraries and remove comments
        # assert returned_cell.original_source == expected_cell.original_source


def get_container_tag_from_ghcr_url(ghcr_url, token):
    """
    Given a GHCR image URL, fetch the tags of the latest version of the
    container package.

    Args:
        ghcr_url (str): e.g.
            'ghcr.io/naavre/flavors/naavre-fl-vanilla-cell-build:latest'
        token (str): GitHub token with 'read:packages' permission

    Returns:
        list: Tags for the latest version or None on error
    """
    try:
        # Parse components
        without_prefix = ghcr_url.replace("ghcr.io/", "")
        parts = without_prefix.split("/")
        org = parts[0]
        package_name_with_tag = "/".join(parts[1:])
        if ":" in package_name_with_tag:
            package_name, _ = package_name_with_tag.rsplit(":", 1)
        else:
            package_name = package_name_with_tag

        # Prepare API request
        encoded_package = quote(package_name, safe='')
        url = (f"https://api.github.com/orgs/{org}/packages/container/"
               f"{encoded_package}/versions")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

        # Send request
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data:
                if parts[2].endswith('latest'):
                    for version in data:
                        container = version["metadata"]["container"]
                        if 'latest' in container["tags"]:
                            tags = container["tags"]
                            for tag in tags:
                                if tag.startswith("v"):
                                    return tag
                    return None
                else:
                    image_tag = parts[2].split(":")[-1]
                    for version in data:
                        tags = version["metadata"]["container"].get("tags", [])
                        if image_tag in tags:
                            return tags
                    print(f"Tag {image_tag} not found in versions.")
                    return None
            else:
                print("No versions found.")
                return None
        else:
            print(f"GitHub API error {response.status_code}: {response.text}")
            return None

    except Exception as e:
        print(f"Error: {e}")
        return None
