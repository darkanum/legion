# Lane: security

Who can reach what, and what leaves the system. Read each changed entry point as an attacker would: what can I send,
and what do I get back? Report only what the diff introduces or makes reachable.

## SEC-01 Every entry point checks who is calling

A new or changed endpoint, route, handler, job trigger or webhook requires authentication unless it is public on
purpose, and the PR says so.
**Default:** blocker.

## SEC-02 Access is checked on the object, not only the route

The code checks that the caller may act on this record (owner, tenant, role), not only that they are signed in.
An id taken from the request is never trusted alone.
**Default:** blocker.

## SEC-03 No injection

Queries, shell commands, templates, paths and expressions built from input use parameters or safe builders. No
string concatenation of input into SQL, a shell, `eval`, a file path or an LDAP or NoSQL filter.
**Default:** blocker.

## SEC-04 Output is encoded for its context

Input rendered into HTML, attributes, URLs, scripts or emails is escaped by the framework. Any raw-HTML escape hatch
gets sanitised input and a reason.
**Default:** blocker for a reachable script injection; important otherwise.

## SEC-05 Secrets stay secret

No secret in code, tests, logs, error messages, client bundles or URLs. Secrets come from the secret store or the
environment, and the client never receives a server secret.
**Default:** blocker.

## SEC-06 Sensitive data is minimised

Responses, logs, analytics events and third-party calls carry only the fields they need. No password hash, token,
full card or identity number, or another user's data.
**Default:** blocker for a leak of another user's data or a credential; important otherwise.

## SEC-07 Redirects, uploads and fetches are bounded

A redirect target from input is checked against an allowlist. An upload checks type and size and never runs or
serves the file as code. A server-side fetch to a URL from input is restricted (no internal hosts).
**Default:** blocker.

## SEC-08 Crypto and tokens use the platform

Hashing, signing, encryption and random tokens use the platform's vetted functions, with a secure random source. No
home-made crypto, no fast hash for passwords, no comparison of secrets that leaks timing.
**Default:** blocker.

## SEC-09 Security settings are not weakened

The PR does not loosen CORS, CSP, cookie flags, TLS checks, rate limits or CSRF protection without a stated reason.
**Default:** important; blocker when the loosening is broad (as `*` origins with credentials).

## SEC-10 New dependencies and services are trusted

A new package or third-party service that sees user data or runs at build time is maintained, widely used, and has
no known critical advisory.
**Default:** important.

## Out of scope

Style, naming and test coverage belong to Gate 1 and the tests lane. A theoretical risk the diff does not make
reachable is a `followup` at most.
