# Terragrunt GitHub Actions 

Terragrunt is a popular open-source tool that works in conjunction with Terraform, another infrastructure-as-code tool. It helps manage and organize your Terraform configurations, making it easier to work with large or complex infrastructure deployments. Terragrunt adds several features and improvements on top of Terraform


This is a suite of terragrunt related GitHub Actions that can be used together to build effective Infrastructure as Code workflows.

## Actions
See the documentation for the available actions:

- [wayofdev/gh-action-terragrunt-plan](gh-action-terragrunt-plan)
- [wayofdev/gh-action-terragrunt-apply](gh-action-terragrunt-apply)


## Example Usage
Here are some examples of how the terragrunt actions can be used together in workflows.

### Terragrunt plan 

Terraform plans typically need to be reviewed by a human before being applied.
Fortunately, GitHub has a well established method for requiring human reviews of changes - a Pull Request.

We can use PRs to safely plan and apply infrastructure changes.


You can make GitHub enforce this using branch protection.

#### plan.yaml
This workflow runs on changes to a PR branch. It generates a terraform plan for each module in provided path and attaches it to the PR as a comment.

```yaml
name: Create a terraform plan

on:
  workflow_call:

jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout the codebase
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Create plan
        uses: wayofdev/gh-action-terragrunt-plan@v1
        with:
          path: my-terraform-config
          tg_version: '0.52.4'
          tf_version: '1.5.7'
          destroy: false
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
```

#### apply.yaml
This workflow runs when the PR is merged into the main branch, and applies the planned changes.

```yaml
name: Apply terraform plan

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
    env:
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    steps:
      - name: Checkout
        uses: actions/checkout@v3

      - name: Apply plan
        uses: wayofdev/gh-action-terragrunt-apply@v1
        with:
          path: my-terraform-config
```
