import json
import os

from app.models.containerizer_payload import ContainerizerPayload
from app.main import _get_containerizer


if os.path.exists('resources'):
    base_path = 'resources'
elif os.path.exists('app/tests/resources/'):
    base_path = 'app/tests/resources/'
else:
    raise RuntimeError('cannot find test resources')


def _load_payload():
    cell_dir = os.path.join(
        base_path, 'notebook_cells/check-var-types-dev-user-name-domain-com')
    with open(os.path.join(cell_dir, 'cell.json')) as f:
        cell = json.load(f)
    with open(os.path.join(cell_dir, 'payload_containerize.json')) as f:
        payload = json.load(f)
    payload['cell'] = cell
    return payload


def test_dockerfile_default_uses_micromamba_install():
    payload = ContainerizerPayload(**_load_payload())
    containerizer = _get_containerizer(payload)
    dockerfile = containerizer.build_docker()
    assert 'micromamba install -y -n venv -f environment.yaml' in dockerfile
    assert 'wget' not in dockerfile


def test_dockerfile_with_environment_url_skips_micromamba():
    payload_dict = _load_payload()
    payload_dict['environment_url'] = (
        'https://example.com/bucket/env.tar.gz?sig=abc')
    payload = ContainerizerPayload(**payload_dict)
    containerizer = _get_containerizer(payload)
    dockerfile = containerizer.build_docker(
        environment_url=payload.environment_url)
    assert 'micromamba install' not in dockerfile
    assert 'wget -qO /tmp/env.tar.gz' in dockerfile
    assert payload.environment_url in dockerfile
    assert '/venv/bin/conda-unpack' in dockerfile
