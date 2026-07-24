import json
import os
import random
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app

if os.path.exists('resources'):
    base_path = 'resources'
elif os.path.exists('app/tests/resources/'):
    base_path = 'app/tests/resources/'
client = TestClient(app)


def get_random_test_case(cells_dirs):
    random_index = random.randint(0, len(cells_dirs) - 1)
    cell_dir = cells_dirs[random_index]

    cell_path = os.path.join(cell_dir, 'cell.json')
    while cell_path is None or not os.path.exists(cell_path):
        random_index = random.randint(0, len(cells_dirs) - 1)
        cell_dir = cells_dirs[random_index]
    with open(cell_path) as f:
        print('Testing containerize for cell: ' + cell_path)
        cell = json.load(f)

    payload_path = os.path.join(cell_dir, 'payload_containerize.json')
    with open(payload_path) as f:
        print('                 with payload: ' + payload_path)
        payload = json.load(f)

    payload['cell'] = cell

    return payload


def test_race_conditions():
    notebook_cells_dir = os.path.join(base_path, 'notebook_cells')
    cells_dirs = [f.path for f in os.scandir(notebook_cells_dir) if f.is_dir()]

    # Pick random cell files
    containerizer_json_payload_1 = get_random_test_case(cells_dirs)
    containerizer_json_payload_2 = get_random_test_case(cells_dirs)

    # Create a thread to do a POST request to /containerize/ concurrently
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(make_containerize_request,
                            containerizer_json_payload_1),
            executor.submit(make_containerize_request,
                            containerizer_json_payload_2)
        ]
        results = [f.result() for f in futures]
    for result in results:
        assert result is not None
    print(results)

    # Test it on the same cell
    containerizer_json_payload_3 = get_random_test_case(cells_dirs)

    # Create a thread to do a POST request to /containerize/ concurrently
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(make_containerize_request,
                            containerizer_json_payload_3),
            executor.submit(make_containerize_request,
                            containerizer_json_payload_3)
        ]
        results = [f.result() for f in futures]

    print(results)
    for result in results:
        assert result is not None


def make_containerize_request(payload):
    containerize_response = client.post(
        '/containerize/',
        headers={'Authorization': 'Bearer ' + os.getenv('AUTH_TOKEN')},
        json=payload,
    )
    return containerize_response.json()
