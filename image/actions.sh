#!/bin/bash

set -euo pipefail

# shellcheck source=../workflow_commands.sh
source /usr/local/workflow_commands.sh

function debug() {
    debug_cmd ls -la /root
    debug_cmd pwd
    debug_cmd ls -la
    debug_cmd ls -la "$HOME"
    debug_cmd printenv
    debug_file "$GITHUB_EVENT_PATH"
    echo
}

function setup() {
    if [[ "$INPUT_PATH" == "" ]]; then
        error_log "input 'path' not set"
        exit 1
    fi

    if [[ ! -d "$INPUT_PATH" ]]; then
        error_log "Path does not exist: \"$INPUT_PATH\""
        exit 1
    fi

    if [[ ! -v TERRAFORM_ACTIONS_GITHUB_TOKEN ]]; then
        if [[ -v GITHUB_TOKEN ]]; then
            export TERRAFORM_ACTIONS_GITHUB_TOKEN="$GITHUB_TOKEN"
        fi
    fi
    
    if ! github_comment_react +1 2>"$STEP_TMP_DIR/github_comment_react.stderr"; then
        debug_file "$STEP_TMP_DIR/github_comment_react.stderr"
    fi

    start_group "Installing Terragrunt and Terraform"

    # install terragrung and terraform
    local TG_VERSION
    local TF_VERSION
    
    if [[ -v INPUT_TG_VERSION ]]; then
        TG_VERSION=$INPUT_TG_VERSION
    fi

    if [[ -v INPUT_TF_VERSION ]]; then
        TF_VERSION=$INPUT_TF_VERSION
    fi

    curl -Lo /usr/local/bin/terragrunt "https://github.com/gruntwork-io/terragrunt/releases/download/v${TG_VERSION}/terragrunt_linux_amd64"
    chmod +x /usr/local/bin/terragrunt
    curl -o /tmp/terraform_${TF_VERSION}_linux_amd64.zip https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip
    unzip /tmp/terraform_${TF_VERSION}_linux_amd64.zip -d /usr/local/bin/
    chmod +x /usr/local/bin/terraform

    end_group

    detect_tfmask
}

function set_common_plan_args() {
    PLAN_ARGS=""
    PARALLEL_ARG=""

    if [[ "$INPUT_PARALLELISM" -ne 0 ]]; then
        PARALLEL_ARG="--terragrunt-parallelism $INPUT_PARALLELISM"
    fi

    if [[ -v INPUT_DESTROY ]]; then
        if [[ "$INPUT_DESTROY" == "true" ]]; then
            PLAN_ARGS="$PLAN_ARGS -destroy"
        fi
    fi
    export PLAN_ARGS
    export PARALLEL_ARG
}

function plan() {

    # shellcheck disable=SC2086
    debug_log terragrunt run-all plan --terragrunt-download-dir $TG_CACHE_DIR -input=false -no-color -detailed-exitcode -lock-timeout=300s $PARALLEL_ARG -out=plan.out '$PLAN_ARGS'  # don't expand PLAN_ARGS

    # Get a list of all modules in the provided path
    MODULE_PATHS=$(terragrunt output-module-groups --terragrunt-working-dir $INPUT_PATH|jq -r 'to_entries | .[].value[]')
    export MODULE_PATHS

    start_group "List of modules found in the provided input path"
    for p in $MODULE_PATHS; do
        echo "- ${INPUT_PATH}${p#*${INPUT_PATH#./}}"
    done
    end_group

    set +e
    # shellcheck disable=SC2086
    start_group "Generating plan"
    (
        (cd "$INPUT_PATH" && terragrunt run-all plan --terragrunt-download-dir $TG_CACHE_DIR -input=false -no-color -detailed-exitcode -lock-timeout=300s $PARALLEL_ARG -out=plan.out $PLAN_ARGS) \
            2>"$STEP_TMP_DIR/terraform_plan.stderr" \
            | $TFMASK
        wait
    )
    end_group

    # Generate text file for each plan
    start_group "Generating plan it text format"
    # shellcheck disable=SC2034
    for i in $MODULE_PATHS; do
        plan_name=${i//\//___}
        terragrunt show plan.out --terragrunt-working-dir $i -no-color --terragrunt-download-dir $TG_CACHE_DIR 2>"$STEP_TMP_DIR/terraform_show_plan.stderr" \
            |tee $PLAN_OUT_DIR/$plan_name
    done
    end_group
    set -e
}

function apply() {

    # shellcheck disable=SC2086
    debug_log terragrunt run-all apply --terragrunt-download-dir $TG_CACHE_DIR -input=false -no-color -auto-approve -lock-timeout=300s $PARALLEL_ARG '$PLAN_ARGS' plan.out

    set +e
    start_group "Applying plan sequentially"
    (
        for i in $MODULE_PATHS; do
            plan_name=${i//\//___}
            if grep -q "No changes." $PLAN_OUT_DIR/$plan_name; then
                echo "There is no changes in the module ${INPUT_PATH}${i#*${INPUT_PATH#./}}, skiping plan apply for it"
                continue
            else
                (cd $i && terragrunt run-all apply --terragrunt-download-dir $TG_CACHE_DIR -input=false -no-color -auto-approve -lock-timeout=300s $PARALLEL_ARG $PLAN_ARGS plan.out) \
                    2>"$STEP_TMP_DIR/terraform_apply_error/${plan_name}.stderr" \
                    | $TFMASK \
                    | tee /dev/fd/3 "$STEP_TMP_DIR/terraform_apply_stdout/${plan_name}.stdout"
            fi
        done
        wait
    )
    end_group
    set -e
}

function apply_all() {

    # shellcheck disable=SC2086
    debug_log terragrunt run-all apply --terragrunt-download-dir $TG_CACHE_DIR -input=false -no-color -auto-approve -lock-timeout=300s $PARALLEL_ARG '$PLAN_ARGS' plan.out

    set +e
    start_group "Applying plan parallel"
    # shellcheck disable=SC2086
    (
        (cd "$INPUT_PATH" && terragrunt run-all apply --terragrunt-download-dir $TG_CACHE_DIR -input=false -no-color -auto-approve -lock-timeout=300s $PARALLEL_ARG $PLAN_ARGS plan.out) \
            2>"$STEP_TMP_DIR/terraform_apply.stderr" \
            | $TFMASK \
            | tee /dev/fd/3 "$STEP_TMP_DIR/terraform_apply.stdout"
        wait
    )
    end_group
    set -e
}

function job_markdown_ref() {
    echo "[${GITHUB_WORKFLOW} #${GITHUB_RUN_NUMBER}](${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID})"
}

function detect_tfmask() {
    TFMASK="tfmask"
    if ! hash tfmask 2>/dev/null; then
        TFMASK="cat"
    fi

    export TFMASK
}

function output() {
    debug_log terragrunt run-all output -json
    (cd "$INPUT_PATH" && terragrunt run-all output -json | convert_output)
}

function update_status() {
    local status="$1"

    if ! STATUS="$status" github_pr_comment status 2>"$STEP_TMP_DIR/github_pr_comment.stderr"; then
        debug_file "$STEP_TMP_DIR/github_pr_comment.stderr"
    else
        debug_file "$STEP_TMP_DIR/github_pr_comment.stderr"
    fi
}

function random_string() {
    python3 -c "import random; import string; print(''.join(random.choice(string.ascii_lowercase) for i in range(8)))"
}

function fix_owners() {
    debug_cmd ls -la "$GITHUB_WORKSPACE"
    if [[ -d "$GITHUB_WORKSPACE/.gh-actions-terragrunt" ]]; then
        chown -R --reference "$GITHUB_WORKSPACE" "$GITHUB_WORKSPACE/.gh-actions-terragrunt" || true
        debug_cmd ls -la "$GITHUB_WORKSPACE/.gh-actions-terragrunt"
    fi
    if [[ -d "$GITHUB_WORKSPACE/$INPUT_CACHE_FOLDER" ]]; then
        chown -R --reference "$GITHUB_WORKSPACE" "$GITHUB_WORKSPACE/$INPUT_CACHE_FOLDER" || true
        debug_cmd ls -la "$GITHUB_WORKSPACE/$INPUT_CACHE_FOLDER"
    fi

    debug_cmd ls -la "$HOME"
    if [[ -d "$HOME/.gh-actions-terragrunt" ]]; then
        chown -R --reference "$HOME" "$HOME/.gh-actions-terragrunt" || true
        debug_cmd ls -la "$HOME/.gh-actions-terragrunt"
    fi
    if [[ -d "$HOME/.terraform.d" ]]; then
        chown -R --reference "$HOME" "$HOME/.terraform.d" || true
        debug_cmd ls -la "$HOME/.terraform.d"
    fi

    if [[ -d "$INPUT_PATH" ]]; then
        debug_cmd find "$INPUT_PATH" -regex '.*/zzzz-gh-actions-terragrunt-[0-9]+\.auto\.tfvars' -print -delete || true
    fi
}

export TF_IN_AUTOMATION=true

if [[ "$INPUT_CREATE_CACHE_FOLDER_IN_WORKSPACE" == "true" ]]; then
    CACHE_PATH=${GITHUB_WORKSPACE}
else
    CACHE_PATH="/tmp"
fi

if [[ "$INPUT_USE_TF_PLUGIN_CACHE_FOLDER" == "true" ]]; then
    TF_PLUGIN_CACHE_DIR="${CACHE_PATH}/${INPUT_CACHE_FOLDER}/${INPUT_TF_PLUGIN_CACHE_FOLDER}"
    mkdir -p $TF_PLUGIN_CACHE_DIR
    readonly TF_PLUGIN_CACHE_DIR
    export TF_PLUGIN_CACHE_DIR
fi

STEP_TMP_DIR="/tmp"
PLAN_OUT_DIR="/tmp/plan"
TG_CACHE_DIR="${CACHE_PATH}/${INPUT_CACHE_FOLDER}/${INPUT_TG_CACHE_FOLDER}"

JOB_TMP_DIR="$HOME/.gh-actions-terragrunt"
WORKSPACE_TMP_DIR=".gh-actions-terragrunt/$(random_string)"
mkdir -p $PLAN_OUT_DIR $TG_CACHE_DIR
mkdir -p $STEP_TMP_DIR/terraform_apply_stdout
mkdir -p $STEP_TMP_DIR/terraform_apply_error
readonly STEP_TMP_DIR JOB_TMP_DIR WORKSPACE_TMP_DIR PLAN_OUT_DIR TG_CACHE_DIR
export STEP_TMP_DIR JOB_TMP_DIR WORKSPACE_TMP_DIR PLAN_OUT_DIR TG_CACHE_DIR

trap fix_owners EXIT
