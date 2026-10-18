# The one image every Legion agent runs in. An agent mounts its own entrypoint at run time; nothing agent-specific is
# baked in here. shared/sandbox/setup.sh builds it as legion-sandbox:local after it writes managed-settings.generated.json.
FROM node:22-bookworm-slim

ARG EXTRA_APT=""
# The Claude Code CLI version, pinned so a CLI release never changes the agents under a running host. Move it by PR;
# LEGION_CLAUDE_CODE_VERSION in legion.env overrides it on one host.
ARG CLAUDE_CODE_VERSION=2.1.302

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git jq ripgrep gnupg ${EXTRA_APT} \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

RUN corepack enable \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

# uid 1001: the run folders on the host are chowned to it.
RUN useradd --create-home --uid 1001 --shell /bin/bash agent

# Headless Claude Code reads its permissions from here; a tool outside the allow list is denied, never prompted.
COPY managed-settings.generated.json /etc/claude-code/managed-settings.json
COPY bin/legion-axiom bin/legion-posthog /usr/local/bin/
COPY lib/boot.sh /usr/local/lib/legion/boot.sh
RUN chmod 0755 /usr/local/bin/legion-axiom /usr/local/bin/legion-posthog && chmod 0644 /usr/local/lib/legion/boot.sh

USER agent
WORKDIR /home/agent
ENV PNPM_HOME=/home/agent/.local/share/pnpm \
    PATH=/home/agent/.local/share/pnpm:$PATH \
    npm_config_store_dir=/home/agent/.local/share/pnpm/store
RUN mkdir -p /home/agent/.local/share/pnpm/store /home/agent/.claude
