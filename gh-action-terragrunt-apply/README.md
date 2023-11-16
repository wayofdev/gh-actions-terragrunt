# gh-action-terragrunt-apply action

This Terragrunt action based on [dflook/terraform-github-actions](https://github.com/dflook/terraform-github-actions).

This action applies a Terraform plan for each module in the provided path. The default behaviour is to apply the plan that has been added to a PR using the wayofdev/gh-action-terragrunt-plan action.

If the plan is not found or has changed, then the apply action will fail. This is to ensure that the action only applies changes that have been reviewed by a human.

You can instead set `auto_approve: true` which will generate a plan and apply it immediately, without looking for a plan attached to a PR.

**NOTE:** This github action uses default terragrunt cache folder `.terragrunt-cache` to create plan and then to read it. 
Don't use terragrunt_download setting in your terragrunt code and also don't clear cache. Otherwise the action won't work.

## Inputs

These input values must be the same as any wayofdev/gh-action-terragrunt-plan for the same configuration. (unless auto_approve: true)

* `path`

  Path to the Terragrunt root module to apply

  - Type: string
  - Optional
  - Default: The action workspace

* `tg_version`

  Terragrunt version required to run the plan

  - Type: string
  - Optional
  - Default: `0.52.4`

* `tf_version`

  Terraform version required to run the plan

  - Type: string
  - Optional
  - Default: `1.5.7`

* `parallelism`

  Limit the number of concurrent operations

  - Type: number
  - Optional
  - Default: The terraform default (10)

* `label`

  A friendly name for the environment the configuration is for.
  This will be used in the PR comment for easy identification.

  If set, must be the same as the `label` used in the corresponding `terragrunt-plan-all` command.

  - Type: string
  - Optional

* `destroy`

  Set to true to destroy all resources.

  This generates and applies plans in destroy mode.

  - Type: boolean
  - Optional
  - Default: false

* `auto_approve`

  When set to true, generated plans are always applied.

  The default is false, which requires plans to have been approved through a pull request.

  - Type: bool
  - Optional
  - Default: false

## Environment Variables

* `GITHUB_TOKEN`

  The GitHub authorization token to use to fetch an approved plans from a PR. This must belong to the same user/app as the token used by the terragrunt-plan-all action. The token provided by GitHub Actions can be used - it can be passed by using the ${{ secrets.GITHUB_TOKEN }} expression, e.g.

  ```yaml
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  ```

  The token provided by GitHub Actions has default permissions at GitHub's whim. You can see what it is for your repo under the repo settings.

  The minimum permissions are `pull-requests: write`.
  It will also likely need `contents: read` so the job can checkout the repo.

  You can also use any other App token that has `pull-requests: write` permission.

  You can use a fine-grained Personal Access Token which has repository permissions:
  - Read access to metadata
  - Read and Write access to pull requests

  You can also use a classic Personal Access Token which has the `repo` scope.

  The GitHub user or app that owns the token will be the PR comment author.

  - Type: string
  - Optional

* `TERRAFORM_ACTIONS_GITHUB_TOKEN`

  When this is set it is used instead of `GITHUB_TOKEN`, with the same behaviour.
  The GitHub Terraform provider also uses the `GITHUB_TOKEN` environment variable, 
  so this can be used to make the github actions and the Terraform provider use different tokens.

  - Type: string
  - Optional

## Workflow events

When adding the plan to a PR comment (`add_github_comment` is set to `true`/`changes-only`), the workflow can be triggered by the following events:

  - pull_request
  - pull_request_review_comment
  - pull_request_target
  - pull_request_review
  - issue_comment, if the comment is on a PR (see below)
  - push, if the pushed commit came from a PR (see below)
  - repository_dispatch, if the client payload includes the pull_request url (see below)

When `auto_approve` is set to true, the workflow can be triggered by any event.

### issue_comment

This event triggers workflows when a comment is made in a Issue, as well as a Pull Request.
Since running the action will only work in the context of a PR, the workflow should check that the comment is on a PR before running.

Also take care to checkout the PR ref.


```yaml
jobs:
  plan:
    if: ${{ github.event.issue.pull_request }}
    runs-on: ubuntu-latest
    env:
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    steps:
      - name: Checkout
        uses: actions/checkout@v3
        with:
          ref: refs/pull/${{ github.event.issue.number }}/merge

      - name: terragrung plan
        uses: wayofdev/gh-action-terragrunt-apply@v1.0.0
        with:
          path: my-terraform-config
```

### push

The pushed commit must have come from a Pull Request. Typically this is used to trigger a workflow that runs on the main branch after a PR has been merged.

### repository_dispatch

This event can be used to trigger a workflow from another workflow. The client payload must include the pull_request api url of where the plan PR comment should be added.

A minimal example payload looks like:
```json
{
  "pull_request": {
    "url": "https://api.github.com/repos/wayofdev/gh-actions-terragrunt/pulls/1"
  }
}
```

## Example usage

### Apply PR approved plans

This example workflow runs for every push to main. If the commit came from a PR that has been merged, applies the plan from the PR.

```yaml
name: Apply

on:
  push:
    branches:
      - main

permissions:
  contents: read
  pull-requests: write

jobs:
  apply:
    runs-on: ubuntu-latest
    name: Apply approved plan
    env:
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    steps:
      - name: Checkout
        uses: actions/checkout@v3

      - name: terraform apply
        uses: wayofdev/gh-action-terragrunt-apply@v1
        with:
          path: my-terraform-config
```

### Always apply changes

This example workflow runs for every push to main. Changes are planned and applied.

```yaml
name: Apply

on:
  push:
    branches:
      - main

jobs:
  apply:
    runs-on: ubuntu-latest
    name: Apply Terraform
    steps:
      - name: Checkout
        uses: actions/checkout@v3

      - name: terraform apply
        uses: dflook/terraform-apply@v1
        with:
          path: my-terraform-config
          auto_approve: true
```
