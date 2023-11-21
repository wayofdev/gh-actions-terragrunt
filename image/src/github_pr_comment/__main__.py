import hashlib
import os
import subprocess
import re
import sys
from pathlib import Path
from typing import (NewType, Optional, cast, Tuple, List)

import canonicaljson

from github_actions.api import GithubApi, IssueUrl, PrUrl
from github_actions.cache import ActionsCache
from github_actions.commands import output
from github_actions.debug import debug
from github_actions.env import GithubEnv
from github_actions.find_pr import find_pr, WorkflowException
from github_actions.inputs import PlanPrInputs
from github_pr_comment.backend_config import complete_config, partial_config
from github_pr_comment.backend_fingerprint import fingerprint
from github_pr_comment.cmp import plan_cmp, remove_warnings, remove_unchanged_attributes
from github_pr_comment.comment import find_comment, TerraformComment, update_comment, serialize, deserialize
from github_pr_comment.hash import comment_hash, plan_hash
from plan_renderer.variables import render_argument_list, Sensitive
from terraform.module import load_module, get_sensitive_variables
from terraform import hcl

Plan = NewType('Plan', str)
Status = NewType('Status', str)

job_cache = ActionsCache(Path(os.environ.get('JOB_TMP_DIR', '.')), 'job_cache')
step_cache = ActionsCache(Path(os.environ.get('STEP_TMP_DIR', '.')), 'step_cache')

env = cast(GithubEnv, os.environ)
github_token = env['TERRAFORM_ACTIONS_GITHUB_TOKEN']
github = GithubApi(env.get('GITHUB_API_URL', 'https://api.github.com'), github_token)

ToolProductName = os.environ.get('TOOL_PRODUCT_NAME', 'Terragrunt')

def job_markdown_ref() -> str:
    return f'[{os.environ["GITHUB_WORKFLOW"]} #{os.environ["GITHUB_RUN_NUMBER"]}]({os.environ["GITHUB_SERVER_URL"]}/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]})'


def job_workflow_ref() -> str:
    return f'Job {os.environ["GITHUB_WORKFLOW"]} #{os.environ["GITHUB_RUN_NUMBER"]} at {os.environ["GITHUB_SERVER_URL"]}/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'


def _mask_backend_config(action_inputs: PlanPrInputs) -> Optional[str]:
    bad_words = [
        'token',
        'password',
        'sas_token',
        'access_key',
        'secret_key',
        'client_secret',
        'access_token',
        'http_auth',
        'secret_id',
        'encryption_key',
        'key_material',
        'security_token',
        'conn_str',
        'sse_customer_key',
        'application_credential_secret'
    ]

    clean = []

    for field in action_inputs.get('INPUT_BACKEND_CONFIG', '').split(','):
        if not field:
            continue

        if not any(bad_word in field for bad_word in bad_words):
            clean.append(field)

    return ','.join(clean)


def format_classic_description(action_inputs: PlanPrInputs) -> str:
    if action_inputs['INPUT_LABEL']:
        return f'Terraform plan for __{action_inputs["INPUT_LABEL"]}__'

    label = f'Terraform plan in __{action_inputs["INPUT_PATH"]}__'

    if backend_config := _mask_backend_config(action_inputs):
        label += f'\nWith backend config: `{backend_config}`'

    return label


def format_description(action_inputs: PlanPrInputs) -> str:    

    mode = ''
    if action_inputs["INPUT_DESTROY"] == 'true':
        mode = '\n:bomb: Planning to destroy all resources'

    if action_inputs['INPUT_LABEL']:
        return f'{ToolProductName} plan for __{action_inputs["INPUT_LABEL"]}__' + mode

    label = f'{ToolProductName} plan in __{action_inputs["INPUT_PATH"]}__'

    label += mode

    return label


def create_plan_hashes(folder_path: str, salt: str) -> Optional[List[dict]]:
    plan_hashes = []

    for file in os.listdir(folder_path):
        file_path = Path(os.path.join(folder_path, file)) 
        hash_section = {}
        hash_section['plan_name'] = file
        hash_section['plan_hash'] = plan_hash(file_path.read_text().strip(), salt)
        plan_hashes.append(hash_section)

    print("PRINTING HASHES")
    print(plan_hashes)
    return plan_hashes


def create_sections(folder_path: str) -> Optional[List[dict]]:
    sections = []

    for file in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file)
        
        module_name = file.replace("___","/")

        section = {}
        body = []
        summary = None
        to_move = 0

        with open(file_path, 'r') as plan:
            lines = plan.readlines()

            for line in lines:
                            
                if line.startswith('No changes') or line.startswith('Error'):
                    summary = line

                if re.match(r'  # \S+ has moved to \S+$', line):
                    to_move += 1

                if line.startswith('Plan:'):
                    summary = line
                    if to_move and 'move' not in summary:
                        summary = summary.rstrip('.') + f', {to_move} to move.'
                
                if line.startswith('Changes to Outputs'):
                    if summary:
                        summary = summary + ' Changes to Outputs.'
                    else:
                        summary = line
                
                body.append(line)
            
        summary = f'{module_name}: {summary}'
        section['summary'] = summary  
        section['body'] = ''.join(body)
        sections.append(section)

    if sections:
        return sections

    # No sections were found in the folder.
    return 'Plan generated.'


def current_user(actions_env: GithubEnv) -> str:
    token_hash = hashlib.sha256(f'dflook/terraform-github-actions/{github_token}'.encode()).hexdigest()
    cache_key = f'token-cache/{token_hash}'

    def graphql() -> Optional[str]:
        graphql_url = actions_env.get('GITHUB_GRAPHQL_URL', f'{actions_env["GITHUB_API_URL"]}/graphql')

        response = github.post(graphql_url, json={
            'query': "query { viewer { login } }"
        })
        debug(f'graphql response: {response.content}')

        if response.ok:
            try:
                return response.json()['data']['viewer']['login']
            except Exception as e:
                pass

        debug('Failed to get current user from graphql')

    def rest() -> Optional[str]:
        response = github.get(f'{actions_env["GITHUB_API_URL"]}/user')
        debug(f'rest response: {response.content}')

        if response.ok:
            user = response.json()

            return user['login']

    if cache_key in job_cache:
        username = job_cache[cache_key]
    else:

        # Not all tokens can be used with graphql
        # There is also no rest endpoint that can get the current login for app tokens :(
        # Try graphql first, then fallback to rest (e.g. for fine grained PATs)

        username = graphql() or rest()

        if username is None:
            debug('Unable to get username for the github token')
            username = 'github-actions[bot]'

        job_cache[cache_key] = username

    debug(f'token username is {username}')
    return username


def get_issue_url(pr_url: str) -> IssueUrl:
    pr_hash = hashlib.sha256(pr_url.encode()).hexdigest()
    cache_key = f'issue-url/{pr_hash}'

    if cache_key in job_cache:
        issue_url = job_cache[cache_key]
    else:
        response = github.get(pr_url)
        response.raise_for_status()
        issue_url = response.json()['_links']['issue']['href']

        job_cache[cache_key] = issue_url

    return cast(IssueUrl, issue_url)


def get_pr() -> PrUrl:
    if 'pr_url' in step_cache:
        pr_url = step_cache['pr_url']
    else:
        try:
            pr_url = find_pr(github, env)
            step_cache['pr_url'] = pr_url
        except WorkflowException as e:
            sys.stderr.write('\n' + str(e) + '\n')
            sys.exit(1)

    return cast(PrUrl, pr_url)


def get_comment(action_inputs: PlanPrInputs) -> TerraformComment:
    if 'comment' in step_cache:
        return deserialize(step_cache['comment'])

    pr_url = get_pr()
    issue_url = get_issue_url(pr_url)
    username = current_user(env)

    legacy_description = format_classic_description(action_inputs)

    headers = {}

    headers['label'] = os.environ.get('INPUT_LABEL') or None

    plan_modifier = {}
    if os.environ.get('INPUT_DESTROY') == 'true':
        plan_modifier['destroy'] = 'true'

    if plan_modifier:
        debug(f'Plan modifier: {plan_modifier}')
        headers['plan_modifier'] = hashlib.sha256(canonicaljson.encode_canonical_json(plan_modifier)).hexdigest()

    backup_headers = headers.copy()

    return find_comment(github, issue_url, username, headers, backup_headers, legacy_description)


def is_approved(folder_path: str, comment: TerraformComment) -> bool:

    for file in os.listdir(folder_path):
        file_path = Path(os.path.join(folder_path, file)) 
        
        for hash in comment.headers.get('plan_hashes'):
            if hash.get('plan_name') == file:
                if hash.get('plan_hash') == plan_hash(file_path.read_text().strip(), comment.issue_url):
                    continue
                else:
                    return False

    debug('Approving plan based on plan hash')     
    return True

def format_plan_text(plan_text: str) -> Tuple[str, str]:
    """
    Format the given plan for insertion into a PR comment
    """

    max_body_size = 50000  # bytes

    def truncate(t):
        lines = []
        total_size = 0

        for line in t.splitlines():
            line_size = len(line.encode()) + 1  # + newline
            if total_size + line_size > max_body_size:
                lines.append('Plan is too large to fit in a PR comment. See the full plan in the workflow log.')
                break

            lines.append(line)
            total_size += line_size

        return '\n'.join(lines)

    if len(plan_text.encode()) > max_body_size:
        # needs truncation
        return 'trunc', truncate(plan_text)
    else:
        return 'text', plan_text


def main() -> int:

    if len(sys.argv) < 2:
        sys.stderr.write(f'''Usage:
    STATUS="<status>" {sys.argv[0]} plan
    STATUS="<status>" {sys.argv[0]} status
    {sys.argv[0]} get plan.txt
    {sys.argv[0]} approved
''')
        return 1

    debug(repr(sys.argv))

    plan_path = os.environ.get('PLAN_OUT_DIR')
    action_inputs = cast(PlanPrInputs, os.environ)

    comment = get_comment(action_inputs)

    status = cast(Status, os.environ.get('STATUS', ''))

    if sys.argv[1] == 'plan':
        description = format_description(action_inputs)

        headers = comment.headers.copy()
        headers['plan_job_ref'] = job_workflow_ref()
        headers['plan_hashes'] = create_plan_hashes(plan_path, comment.issue_url)

        comment = update_comment(
            github,
            comment,
            description=description,
            sections=create_sections(plan_path),
            headers=headers,
            status=status
        )

    elif sys.argv[1] == 'status':
        if comment.comment_url is None:
            debug("Can't set status of comment that doesn't exist")
            return 1
        else:
            comment = update_comment(github, comment, status=status)

    elif sys.argv[1] == 'get':
        if comment.comment_url is None:
            debug("Can't get the plan from comment that doesn't exist")
            return 1

        with open(sys.argv[2], 'w') as f:
            f.write(comment.body)

    elif sys.argv[1] == 'approved':
        
        if comment.comment_url is None:
            sys.stdout.write("Plan not found on PR\n")
            sys.stdout.write("Generate the plan first using the Fenikks/terragrunt-plan-all action. Alternatively set the auto_approve input to 'true'\n")
            output('failure-reason', 'plan-changed')
            sys.exit(1)


        num_of_plan_files = len([name for name in os.listdir(plan_path) if os.path.isfile(os.path.join(plan_path, name))])
        num_of_plans_in_comment = len(comment.sections)
        if num_of_plan_files != num_of_plans_in_comment:
            sys.stdout.write("The number of plans in PR doesn't match the current number of plans.\n")
            sys.stdout.write("Regenerate the plan first using the Fenikks/terragrunt-plan-all action.\n")
            output('failure-reason', 'number-of-plans-changed')
            sys.exit(1)

        if not is_approved(plan_path, comment, ):

            sys.stdout.write("Not applying the plan - it has changed from the plan on the PR\n")
            sys.stdout.write("The plan on the PR must be up to date. Alternatively, set the auto_approve input to 'true' to apply outdated plans\n")
            comment = update_comment(github, comment, status=f':x: Plan not applied in {job_markdown_ref()} (Plan has changed)')

            step_cache['comment'] = serialize(comment)
            return 1

    step_cache['comment'] = serialize(comment)
    return 0

if __name__ == '__main__':
    sys.exit(main())
