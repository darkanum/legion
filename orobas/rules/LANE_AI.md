# Lane: ai

Model calls, prompts, evaluations and cost. A model is an unreliable dependency that bills by the token: treat its
input as an attack surface and its output as untrusted data.

## AI-01 Model output is validated before use

Output that drives code (JSON, a choice, a tool call, a score) is parsed against a schema, with a clear path when it
does not match. Free text from a model is never run, queried or rendered as trusted HTML.
**Default:** blocker when unchecked output reaches a write, a query or a command; important otherwise.

## AI-02 Untrusted text cannot steer the model

User content, documents, web pages and tool results go into the prompt as data, clearly delimited, and the model's
tools are limited to what the task needs. A prompt injection must not reach a privileged action.
**Default:** blocker.

## AI-03 Calls fail well

Every model call has a timeout, a bounded retry with backoff for transient errors, and a fallback or a clear error
for the user. A refusal or an empty answer is handled.
**Default:** important.

## AI-04 Cost and size are bounded

Input and output token limits are set. A loop or an agent has a cap on calls. A large context is trimmed or
retrieved, not sent whole. Repeated prefixes are cached where the provider supports it.
**Default:** important; blocker for an unbounded loop of paid calls.

## AI-05 The model and its settings are explicit

The model name, temperature and other settings come from config, not scattered literals, and a change to them is
stated in the PR body.
**Default:** note; important for a silent model change.

## AI-06 Prompt changes are measured

A change to a prompt, a model or its settings that affects quality comes with an eval run, a test set result or at
least before-and-after examples in the PR.
**Default:** important.

## AI-07 Sensitive data is not sent without need

Only the data the task needs is sent to a model provider, in line with the project's data rules. No secrets in a
prompt.
**Default:** blocker for secrets or data the project forbids sending; important otherwise.

## AI-08 Model calls are observable

Each call logs or traces its model, token counts, latency and outcome, without logging sensitive prompt content.
**Default:** note.

## AI-09 Prompts live where people can review them

Prompt text sits in a named template or constant, not built across many string pieces, so a reviewer can read the
whole prompt.
**Default:** note.
