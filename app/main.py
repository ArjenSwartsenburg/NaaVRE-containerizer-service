import datetime
import json
import logging
import os
import time
from threading import Thread
from typing import Annotated
from urllib.parse import urlparse

import cachetools.func
import jsonschema
import jwt
import requests
import uvicorn
from cachetools import TTLCache
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.containerizer_payload import ContainerizerPayload
from app.models.extractor_payload import ExtractorPayload, NotebookExtractorPayload
from app.models.notebook_data import NotebookData
from app.models.workflow_cell import Cell
from app.models.vl_config import VLConfig
from app.services.base_image.base_image_tags import BaseImageTags
from app.services.cell_extractor.extractor import DummyExtractor
from app.services.cell_extractor.py_extractor import (
    PyExtractor, extract_notebook_imports)
from app.services.cell_extractor.py_header_extractor import PyHeaderExtractor
from app.services.cell_extractor.r_extractor import RExtractor
from app.services.cell_extractor.r_header_extractor import RHeaderExtractor
from app.services.containerizers.c_containerizer import CContainerizer
from app.services.containerizers.julia_containerizer import JuliaContainerizer
from app.services.containerizers.py_containerizer import PyContainerizer
from app.services.containerizers.r_containerizer import RContainerizer
from app.services.repositories.github_service import (GithubService,
                                                      get_content_hash)
from app.settings.service_settings import Settings
from app.utils.openid import OpenIDValidator

security = HTTPBearer()
token_validator = OpenIDValidator()


@cachetools.func.ttl_cache(ttl=6 * 3600)
def load_configuration(source):
    # Check if source is a URL
    print('Loading configuration from ' + source)
    parsed_url = urlparse(source)
    if parsed_url.scheme in ("http", "https"):  # Remote URL
        response = requests.get(source)
        return response.json()
    elif os.path.exists(source):  # Local file (relative or absolute path)
        with open(source, "r", encoding="utf-8") as file:
            data_dict = json.load(file)
        file.close()
        if not data_dict:
            raise Exception('Configuration data_dict is empty')
        return data_dict
    else:
        raise Exception('Configuration source: ' + source + ' not found')


config_file = os.getenv('CONFIG_FILE_URL', 'configuration.json')

conf = None
config_path = None
if os.path.exists(config_file):
    conf = load_configuration(config_file)
    print("Found config_file: "+config_file)
else:
    # Start going up the directory tree until we find the configuration file
    current_dir = os.getcwd()
    while current_dir != '/':
        config_path = os.path.join(current_dir,
                                   os.getenv('CONFIG_FILE_URL',
                                             'configuration.json'))
        if os.path.exists(config_path):
            conf = load_configuration(config_path)
            print("Found config_file: " + config_file)
            break
        current_dir = os.path.dirname(current_dir)

print("config_file: "+config_file)
# Print contents of conf for debugging
with open(config_file, "r", encoding="utf-8") as file:
    print(file.read())

print('configuration loaded: ', conf)
settings = Settings(config=conf)

app = FastAPI(root_path=os.getenv('ROOT_PATH',
                                  '/NaaVRE-containerizer-service'))


if os.getenv('DEBUG', 'false').lower() == 'true':
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

containerization_job_cache = TTLCache(maxsize=1000, ttl=86400)


def valid_access_token(credentials: Annotated[
    HTTPAuthorizationCredentials, Depends(security)],
                       ):
    try:
        return token_validator.validate(credentials.credentials)
    except (jwt.exceptions.InvalidTokenError, jwt.exceptions.PyJWKClientError):
        raise HTTPException(status_code=401, detail='Not authenticated')


@app.get('/base-image-tags')
def get_base_image_tags(
        access_token: Annotated[dict, Depends(valid_access_token)],
        virtual_lab: str):
    vl_conf = settings.get_vl_config(virtual_lab)
    base_image_tags = BaseImageTags(vl_conf.base_image_tags_url)
    return base_image_tags.get_base_image_tags()


def _get_containerizer(containerize_payload: ContainerizerPayload):
    vl_conf = settings.get_vl_config(containerize_payload.virtual_lab)
    if (containerize_payload.cell.kernel.lower() == 'python' or
            containerize_payload.cell.kernel == 'ipython'):
        return PyContainerizer(containerize_payload.cell,
                               vl_conf.module_mapping_url)
    elif (containerize_payload.cell.kernel.lower() == 'r' or
          containerize_payload.cell.kernel.lower() == 'irkernel'):
        return RContainerizer(containerize_payload.cell,
                              vl_conf.module_mapping_url)
    elif containerize_payload.cell.kernel.lower() == 'julia':
        return JuliaContainerizer(containerize_payload.cell,
                                  vl_conf.module_mapping_url)
    elif containerize_payload.cell.kernel.lower() == 'c':
        return CContainerizer(containerize_payload.cell,
                              vl_conf.module_mapping_url)
    else:
        raise ValueError('Unsupported kernel')


def _get_github_service(vl_conf: VLConfig):
    repository_url = vl_conf.cell_github_url
    if repository_url is None:
        raise ValueError('repository_url not set')
    token = vl_conf.cell_github_token
    if not token or not token.strip():
        raise ValueError('cell_github_token is not set')
    logging.debug('Using repository URL: %s', repository_url)
    return GithubService(vl_conf)


def _is_r_kernel(kernel: str) -> bool:
    kl = kernel.lower()
    return kl in ('r', 'irkernel') or kl.startswith('ir')


def _is_python_kernel(kernel: str) -> bool:
    kl = kernel.lower()
    return ('python' in kl or 'ipython' in kl
            or kl.endswith('-py') or kl.endswith('_py')
            or kl == 'py')


def _get_extractor(extractor_payload: ExtractorPayload):
    extractor = None
    notebook = extractor_payload.data.notebook
    cell_index = extractor_payload.data.cell_index
    kernel = extractor_payload.data.kernel
    vl_settings = settings.get_vl_config(extractor_payload.virtual_lab)
    if vl_settings is None:
        raise HTTPException(status_code=400,
                            detail='vl_settings for: ' +
                            extractor_payload.virtual_lab + ' not found')
    if vl_settings.base_image_tags_url is None:
        raise HTTPException(status_code=400,
                            detail='base_image_tags_url for: ' +
                            extractor_payload.virtual_lab + ' not found')
    if vl_settings.module_mapping_url is None:
        raise HTTPException(status_code=400,
                            detail='module_mapping_url for: ' +
                            extractor_payload.virtual_lab + ' not found')
    if vl_settings.cell_github_url is None:
        raise HTTPException(status_code=400,
                            detail='cell_github_url for: ' +
                            extractor_payload.virtual_lab + ' not found')
    if vl_settings.cell_github_token is None:
        raise HTTPException(status_code=400,
                            detail='cell_github_token for: ' +
                            extractor_payload.virtual_lab + ' not found')
    if vl_settings.module_mapping_url is None:
        raise HTTPException(status_code=400,
                            detail='module_mapping_url for: ' +
                            extractor_payload.virtual_lab + ' not found')
    try:
        if notebook.cells[cell_index].cell_type != 'code':
            # dummy extractor for non-code cells (e.g. markdown)
            extractor = DummyExtractor(extractor_payload.data,
                                       vl_settings.base_image_tags_url)
        elif _is_r_kernel(kernel):
            extractor = RHeaderExtractor(extractor_payload.data,
                                         vl_settings.base_image_tags_url)
        elif _is_python_kernel(kernel):
            extractor = PyHeaderExtractor(extractor_payload.data,
                                          vl_settings.base_image_tags_url)
        if not extractor.is_complete():
            if _is_r_kernel(kernel):
                built_in_function_names = (
                    settings.get_built_in_function_names())
                code_extractor = RExtractor(extractor_payload.data,
                                            vl_settings.base_image_tags_url,
                                            built_in_function_names)
            elif _is_python_kernel(kernel):
                code_extractor = PyExtractor(extractor_payload.data,
                                             vl_settings.base_image_tags_url)
            else:
                raise HTTPException(status_code=400,
                                    detail='Unsupported kernel: ' + kernel)
            extractor.add_missing_values(code_extractor)
        return extractor
    except (ValueError, jsonschema.ValidationError) as e:
        raise HTTPException(status_code=422,
                            detail='Error extracting cell: ' + str(e))


def _query_github_job_id(workflow_id, gh_service, wf_creation_utc):
    """
    Function to run in a background thread that polls for the job ID.
    """
    max_retries = 10
    delay = 5  # seconds
    job = gh_service.get_job(wf_id=workflow_id,
                             wf_creation_utc=wf_creation_utc)
    while job is None and max_retries > 0:
        logging.debug('Job not found, retrying in %d seconds...',
                      delay)
        time.sleep(delay)
        job = gh_service.get_job(wf_id=workflow_id,
                                 wf_creation_utc=wf_creation_utc)
        max_retries -= 1
    if job:
        containerization_job_cache[workflow_id] = job['id']


@app.post('/extract_cell')
def extract_cell(access_token: Annotated[dict, Depends(valid_access_token)],
                 extractor_payload: ExtractorPayload):
    extractor_payload.data.set_user_name(access_token['preferred_username'])
    extractor = _get_extractor(extractor_payload)
    if isinstance(extractor, DummyExtractor):
        raise HTTPException(status_code=422,
                            detail='Cell is not a code cell, cannot extract')
    try:
        cell = extractor.get_cell()
    except ValueError as e:
        raise HTTPException(status_code=422,
                            detail='Error extracting cell: ' + str(e))

    if os.getenv('DEBUG', 'false').lower() == 'true':
        test_resource = extractor_payload.model_dump()
        test_resource['cell'] = cell.model_dump()
        with open('/tmp/' + cell.title + '.json', 'w') as f:
            json.dump(test_resource, f, indent=4)
        f.close()
    return cell


@app.post('/extract_notebook')
def extract_notebook(
        access_token: Annotated[dict, Depends(valid_access_token)],
        extractor_payload: NotebookExtractorPayload):
    user_name = access_token['preferred_username']
    kernel = extractor_payload.data.kernel
    vl_settings = settings.get_vl_config(extractor_payload.virtual_lab)
    if vl_settings is None:
        raise HTTPException(status_code=400,
                            detail='vl_settings for: ' +
                            extractor_payload.virtual_lab + ' not found')
    all_dependencies: dict[tuple, dict] = {}
    failed_cells: list[int] = []

    def _add_dependencies(deps):
        for dep in (deps or []):
            key = (dep.get('name'), dep.get('asname'), dep.get('module'))
            all_dependencies[key] = dep

    notebook = extractor_payload.data.notebook
    for i, nb_cell in enumerate(notebook.cells):
        if nb_cell.cell_type != 'code':
            continue
        cell_data = NotebookData(
            cell_index=i,
            kernel=kernel,
            notebook=notebook,
            user_name=user_name,
        )
        # Take any dependencies declared explicitly in a NaaVRE cell header.
        try:
            if _is_r_kernel(kernel):
                extractor = RHeaderExtractor(cell_data, base_image_tags_url='')
                if not extractor.is_complete():
                    code_extractor = RExtractor(
                        cell_data,
                        base_image_tags_url='',
                        built_in_function_names=(
                            settings.get_built_in_function_names()),
                    )
                    extractor.add_missing_values(code_extractor)
                _add_dependencies(extractor.cell_dependencies)
            elif _is_python_kernel(kernel):
                extractor = PyHeaderExtractor(cell_data, base_image_tags_url='')
                _add_dependencies(extractor.cell_dependencies)
            else:
                logging.debug('Unsupported kernel %s for cell %d, skipping',
                              kernel, i)
        except Exception:
            logging.warning('Header extraction failed for cell %d, skipping',
                            i, exc_info=True)
            failed_cells.append(i)

    # Extract the notebook's code imports resiliently (per-cell, magic-aware,
    # no pytype). R is handled entirely by its header/code extractors above.
    if _is_python_kernel(kernel):
        _add_dependencies(extract_notebook_imports(notebook))

    normalized_kernel = 'IRkernel' if _is_r_kernel(kernel) else 'ipython'
    cell = Cell(
        title='notebook',
        base_container_image={},
        dependencies=list(all_dependencies.values()),
        kernel=normalized_kernel,
        original_source='',
    )
    containerizer = (
        RContainerizer if _is_r_kernel(kernel) else PyContainerizer
    )(cell, vl_settings.module_mapping_url)
    headers = {}
    if failed_cells:
        # Surface extraction gaps to the caller instead of failing silently —
        # an env built from partial extraction looks valid but may be missing
        # dependencies.
        headers['X-Extraction-Warnings'] = (
            'extraction failed for cell(s) '
            + ','.join(str(i) for i in failed_cells)
        )
    return Response(
        content=containerizer.build_environment(),
        media_type='text/plain',
        headers=headers,
    )


@app.post('/containerize')
def containerize(access_token: Annotated[dict, Depends(valid_access_token)],
                 containerize_payload: ContainerizerPayload):
    try:
        conteinerizer = _get_containerizer(containerize_payload)
    except ValueError as e:
        raise HTTPException(status_code=422,
                            detail=str(e))
    vl_conf = settings.get_vl_config(containerize_payload.virtual_lab)
    gh = _get_github_service(vl_conf)
    commit_list = []
    cell_contents = conteinerizer.build_script()
    commit_list.append({'contents': cell_contents,
                        'path': containerize_payload.cell.title,
                        'file_name': 'task' + conteinerizer.file_extension})
    environment_contents = conteinerizer.build_environment()
    commit_list.append({'contents': environment_contents,
                        'path': containerize_payload.cell.title,
                        'file_name': 'environment.yaml'})
    docker_template = conteinerizer.build_docker(
        environment_url=containerize_payload.environment_url)
    commit_list.append({'contents': docker_template,
                        'path': containerize_payload.cell.title,
                        'file_name': 'Dockerfile'})
    commit_resp = gh.commit(commit_list=commit_list,
                            force=containerize_payload.force_containerize)
    commit_sha = commit_resp['commit_sha']
    source_url = None
    if commit_sha:
        source_url = (gh.repository_url.replace('.git', '') + '/tree/' +
                      commit_sha + '/' + containerize_payload.cell.title)

    image_version = get_content_hash(cell_contents)[:7]
    container_image = (gh.registry.registry_url + '/' +
                       containerize_payload.cell.title + ':' + image_version)
    containerization_workflow_resp = {'workflow_id': None,
                                      'dispatched_github_workflow': False,
                                      'container_image': container_image}

    force_containerize = containerize_payload.force_containerize
    if commit_resp['content_updated'] or force_containerize:
        containerization_workflow_resp = gh.dispatch_containerization_workflow(
            title=containerize_payload.cell.title,
            image_version=image_version,
            commit_sha=commit_sha)
        wf_creation_utc = datetime.datetime.now(tz=datetime.timezone.utc)
        workflow_id = containerization_workflow_resp.get('workflow_id')
        if workflow_id:
            thread = Thread(
                target=_query_github_job_id,
                args=(workflow_id,
                      gh,
                      wf_creation_utc),
                daemon=True
            )
            thread.start()

        logging.debug('Dispatched containerization workflow for: %s',
                      containerize_payload.cell.title)
    logging.debug('Setting container image: %s', container_image)
    containerize_payload.cell.container_image = container_image
    logging.debug('Containerization workflow response: %s',
                  containerization_workflow_resp)
    return {'workflow_id': containerization_workflow_resp['workflow_id'],
            'dispatched_github_workflow': commit_resp['content_updated'],
            'container_image': container_image,
            'source_url': source_url}


@app.get('/status/{virtual_lab}/{workflow_id}/')
def get_status(
        access_token: Annotated[dict, Depends(valid_access_token)],
        workflow_id: str,
        virtual_lab: str):
    vl_conf = settings.get_vl_config(virtual_lab)
    gh = _get_github_service(vl_conf)
    job_id = containerization_job_cache.get(workflow_id)
    wf_creation_utc = (datetime.datetime.now(tz=datetime.timezone.utc) -
                       datetime.timedelta(hours=24))
    job = gh.get_job(wf_id=workflow_id,
                     job_id=job_id,
                     wf_creation_utc=wf_creation_utc)
    if job is None:
        raise HTTPException(status_code=404,
                            detail='containerization job not found')
    containerization_job_cache[workflow_id] = job['id']

    return {'job': job,
            'workflow_url': workflow_id
            }


if __name__ == '__main__':
    port = int(os.getenv('APP_PORT', 8001))
    uvicorn.run(app, host='0.0.0.0', port=port, log_level='trace')
