ARG FROM_REGISTRY
ARG FROM_REPO
ARG FROM_IMAGE

FROM $FROM_REGISTRY/$FROM_REPO/$FROM_IMAGE:latest

ARG TARGETARCH

ARG FETCH_CHECKSUMS
ARG VERSION=99.0.0

RUN gpg --recv-keys C874011F0AB405110D02105534365D9472D7468F \
 && echo "C874011F0AB405110D02105534365D9472D7468F:6:" | gpg --import-ownertrust
RUN gpg --check-trustdb

COPY src/ /tmp/src/
COPY setup.py /tmp
RUN sed -i "s|version='.*'|version=\'${VERSION}\'|" /tmp/setup.py \
 && pip install /tmp \
 && rm -rf /tmp/src /tmp/setup.py

COPY entrypoints/ /entrypoints/
COPY actions.sh /usr/local/actions.sh
COPY workflow_commands.sh /usr/local/workflow_commands.sh

COPY tools/convert_output.py /usr/local/bin/convert_output
COPY tools/workspace_exists.py /usr/local/bin/workspace_exists
COPY tools/compact_plan.py /usr/local/bin/compact_plan
COPY tools/github_comment_react.py /usr/local/bin/github_comment_react

LABEL org.opencontainers.image.title="GitHub actions for terragrunt"
