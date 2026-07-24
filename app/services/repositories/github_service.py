import datetime
import hashlib
import json
import logging
import uuid
from abc import ABC
from time import sleep

import github
import requests
from github import Github
from github import InputGitTreeElement

from app.models.vl_config import VLConfig
from app.services.container_registries.container_registry import \
    ContainerRegistry
from app.services.repositories.git_repository import GitRepository

logger = logging.getLogger('uvicorn.error')

GITHUB_PREFIX = 'https://github.com/'
GITHUB_API_PREFIX = 'https://api.github.com/'
GITHUB_API_REPOS = GITHUB_API_PREFIX + 'repos'
GITHUB_WORKFLOW_FILENAME = 'build-push-docker.yml'


def get_content_hash(contents):
    contents = contents.encode('utf-8')
    s = hashlib.sha1()
    s.update(('blob %u\0' % len(contents)).encode('utf-8'))
    s.update(contents)
    return s.hexdigest()


class JobNotFoundError(Exception):
    pass


class GithubService(GitRepository, ABC):

    def __init__(self, vl_conf: VLConfig):
        if vl_conf.cell_github_token is None:
            raise ValueError("GitHub token is required for GitHubService")
        self.github = Github(auth=github.Auth.Token(
                                            token=vl_conf.cell_github_token))

        cell_github_url = vl_conf.cell_github_url
        self.token = vl_conf.cell_github_token
        self.owner = cell_github_url.split(GITHUB_PREFIX)[1].split('/')[0]
        self.repository_name = \
            cell_github_url.split(GITHUB_PREFIX)[1].split('/')[1]
        if '.git' in self.repository_name:
            self.repository_name = self.repository_name.split('.git')[0]
        self.gh_repository = self.github.get_repo(
            self.owner + '/' + self.repository_name)
        self.dispatches_url = (GITHUB_API_REPOS + '/' + self.owner + '/' +
                               self.repository_name + '/actions/workflows/' +
                               GITHUB_WORKFLOW_FILENAME + '/dispatches')
        self.commits_url = (GITHUB_API_REPOS + '/' + self.owner + '/' +
                            self.repository_name + '/commits')
        self.registry = ContainerRegistry(registry_url=vl_conf.registry_url,
                                          token=vl_conf.cell_github_token)
        self.repository_url = cell_github_url

    def commit(self, commit_list=None, force=False):
        logger.debug('Committing commit_list: ' + str(commit_list) +
                     ' force: ' + str(force))
        content_updated = False
        tree_elements = []
        image_info = self.registry.query_registry_for_image(
            commit_list[0]['path'])
        if not image_info:
            force = True
        for commit_item in commit_list:
            commit_path = commit_item["path"] + '/' + commit_item['file_name']
            local_hash = get_content_hash(commit_item["contents"])
            remote_hash = None
            try:
                remote_hash = self.gh_repository.get_contents(commit_path).sha
            except github.GithubException:
                force = True
            if remote_hash and local_hash != remote_hash or force:
                content_updated = True
                blob = self.gh_repository.create_git_blob(
                            commit_item["contents"],
                            "utf-8")
                tree_elements.append(InputGitTreeElement(path=commit_path,
                                                         mode="100644",
                                                         type="blob",
                                                         sha=blob.sha))
        commit_sha = None
        if content_updated or force:
            base_tree = self.gh_repository.get_git_tree(sha="main")
            new_tree = self.gh_repository.create_git_tree(tree=tree_elements,
                                                          base_tree=base_tree)
            # Fetch and merge the latest changes from the remote branch
            self.github.get_repo(
                self.owner + '/' + self.repository_name).get_branch("main")
            parent_commit = self.gh_repository.get_git_commit(
                self.gh_repository.get_branch("main").commit.sha)

            # Create a commit
            commit_message = commit_list[0]['path']
            new_commit = (self.gh_repository.
                          create_git_commit(message=commit_message,
                                            tree=new_tree,
                                            parents=[parent_commit]))

            # Update the reference to point to the new commit
            main_ref = self.gh_repository.get_git_ref("heads/main")
            try:
                main_ref.edit(sha=new_commit.sha)
                commit_sha = new_commit.sha
            except github.GithubException as e:
                # Another request may have advanced main in the meantime.
                # Return current HEAD so downstream calls still have a valid
                # ref.
                commit_sha = self.gh_repository.get_branch("main").commit.sha
                error_message = str(e).lower()
                if ("not a fast-forward" not in error_message and
                        "not a fast forward" not in error_message):
                    raise Exception(
                        "Update failed: not a fast-forward. Ensure the branch "
                        "is up-to-date.") from e
        logger.debug('Content updated: ' + str(content_updated))
        return {"content_updated": content_updated, 'commit_sha': commit_sha}

    def dispatch_containerization_workflow(self, title=None,
                                           image_version=None,
                                           commit_sha=None):
        count = 0
        while not self.__is_context_available(title, ref=commit_sha):
            logger.debug('Waiting for context to be available')
            sleep(5)
            count += 1
            if count > 10:
                raise Exception('Context in ' + title + ' is not available')
        wf_id = str(uuid.uuid4())
        json_data = {
                'ref': 'main',
                'inputs': {
                    'build_dir': title,
                    'commit_sha': commit_sha,
                    'dockerfile': 'Dockerfile',
                    'image_repo': self.registry.registry_url,
                    'image_tag': title,
                    'id': wf_id,
                    'image_version': image_version,
                }
            }
        git_resp = requests.post(
            url=self.dispatches_url,
            json=json_data,
            verify=False,
            headers={'Accept': 'application/vnd.github.v3+json',
                     'Authorization': 'token ' + self.token}
        )
        if (git_resp.status_code != 201 and git_resp.status_code != 200 and
                git_resp.status_code != 204):
            raise Exception('Error dispatching workflow: ' + git_resp.text)
        logger.debug('Workflow dispatched: ' + str(git_resp.status_code))
        resp = {'workflow_id': wf_id,
                'source_url': None}
        return resp

    def get_job(self,
                wf_id=None,
                wf_creation_utc=None,
                job_id=None
                ):
        f""" Find Github workflow job

        If job_id is set, retrieve it through
        https://api.github.com/repos/{self.owner}/{self.repository_name}
        /actions/jobs/{job_id}

        Else, get all workflows runs created around wf_creation_utc through
        https://api.github.com/repos/{self.owner}/{self.repository_name}
        /actions/runs
        and find the one matching {wf_id}
        """
        logger.debug('Getting job for workflow: ' + str(wf_id))
        if job_id:
            jobs_url = (GITHUB_API_REPOS + '/' + self.owner + '/' +
                        self.repository_name + '/actions/jobs/' + str(job_id))
            self.wait_for_github_api_resources()
            job = self.get_github_workflow_jobs(jobs_url)
            logger.debug('get_job- Found job with :job_id ' + str(job))
            return job
        self.wait_for_github_api_resources()
        job = self.find_job_by_name(job_name=wf_id,
                                    wf_creation_utc=wf_creation_utc)
        logger.debug('get_job- Found job: ' + str(job))
        return job

    def wait_for_github_api_resources(self):
        rate_limit = self.github.get_rate_limit()
        while rate_limit.core.remaining <= 0:
            reset = rate_limit.core.reset
            # Calculate remaining time for reset
            remaining_time = (reset.timestamp() - datetime.datetime.now().
                              timestamp())
            logger.debug(f'Remaining time for reset: {remaining_time} s')
            logger.debug('API rate exceeded, waiting')
            logger.debug(f'Sleeping for: {remaining_time + 1}')
            sleep(remaining_time + 1)
            rate_limit = self.github.get_rate_limit()

    def get_github_workflow_runs(self, t_utc=None):
        workflow_runs_url = (GITHUB_API_REPOS + '/' + self.owner + '/' +
                             self.repository_name + '/actions/runs')
        if t_utc:
            t_start = (t_utc - datetime.timedelta(minutes=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            # Get t_stop as now
            t_stop = datetime.datetime.now(datetime.UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            workflow_runs_url += f"?created={t_start}..{t_stop}"
        headers = {'Accept': 'application/vnd.github.v3+json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        workflow_runs = requests.get(url=workflow_runs_url, verify=False,
                                     headers=headers)
        if workflow_runs.status_code != 200:
            return None
        workflow_runs_json = json.loads(workflow_runs.text)
        return workflow_runs_json

    def get_github_workflow_jobs(self, jobs_url=None):
        headers = {'Accept': 'application/vnd.github.v3+json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        jobs = requests.get(url=jobs_url, verify=True,
                            headers=headers)
        if jobs.status_code == 200 and jobs:
            return json.loads(jobs.text)
        else:
            return None

    def find_job_by_name(self, job_name=None, wf_creation_utc=None):
        logger.debug('Finding job by name: ' + job_name)
        runs = self.get_github_workflow_runs(
            t_utc=wf_creation_utc)
        logger.debug('Got runs: ' + str(len(runs)))
        return self.get_github_workflow_job(job_name=job_name,
                                            runs=runs['workflow_runs'])

    def get_github_workflow_job(self, job_name=None, runs=None):
        for run in runs:
            jobs_url = run['jobs_url']
            self.wait_for_github_api_resources()
            jobs = self.get_github_workflow_jobs(jobs_url)
            for job in jobs['jobs']:
                if job['name'] == job_name:
                    job['head_sha'] = run['head_sha']
                    return job
        return None

    def __is_context_available(self, path=None, ref=None):
        found_docker = False
        found_env = False
        found_task = False
        try:
            if ref:
                contents = self.gh_repository.get_contents(path, ref=ref)
            else:
                contents = self.gh_repository.get_contents(path)
            for content in contents:
                if content.name == 'Dockerfile':
                    found_docker = True
                if content.name == 'environment.yaml':
                    found_env = True
                if content.name == 'task.py' or content.name == 'task.R':
                    found_task = True
            if found_docker and found_env and found_task:
                return True
            else:
                logger.debug('found_docker: ' + str(found_docker) + ' ' +
                             'found_env: ' + str(found_env) + ' ' +
                             'found_task: ' + str(found_task))
                return False
        except github.GithubException:
            logger.debug('GithubException: ' + path)
            return False
