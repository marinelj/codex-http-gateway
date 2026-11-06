# Security and privacy

## Keep the running service private

This is a single-operator service. It has no gateway authentication. Every client
that can reach the HTTP endpoints can read shared history, inspect project paths,
submit prompts, and cancel runs. The default CLI permission is
`danger-full-access`, with `approval_policy="never"`.

Keep the default loopback bind address. For remote access, use a private tunnel or
an authenticated proxy that restricts access to you. A path prefix such as `/codex`
provides routing, not access control. Publishing this source repository does not
require publishing a running instance.

The thread catalog and saved-message endpoints can reveal conversations from other
projects in the same Codex home, even when `GATEWAY_ALLOW_OTHER_WORKSPACES=false`.
That setting controls sending to those projects, not history visibility.

Choose `read-only` or `workspace-write` when appropriate. These settings constrain
execution; they do not authenticate HTTP callers or redact conversation content.
The CLI inherits the host environment except for gateway settings and selected
session identifiers. Run with only the credentials and filesystem access needed.

## Keep credentials and runtime data out of Git

Never commit or upload:

- Local `.env` files, credential-bearing proxy URLs, API keys, or private keys.
- Codex authentication files, credential-store exports, or a copied `.codex` home.
- Session transcripts, downloaded chat history, or private screenshots.
- Gateway databases, SQLite sidecar files, service logs, or backups.

The gateway stores prompts, model output, tool events, and CLI diagnostics without
secret redaction. These can contain credentials if a prompt or tool exposes them.
Review logs and transcripts before sharing them. The `/v1/usage` endpoint selects
usage fields rather than returning the entire account response; this does not
make other endpoints or model output safe for public sharing.

`.gitignore` excludes common local credential and runtime files. Ignore rules do
not protect files already tracked or explicitly added with `git add -f`.

## Check a release

Run a full-history scan with [Gitleaks](https://github.com/gitleaks/gitleaks), plus
a current-file scan that includes any new files you intend to publish:

```sh
gitleaks git . --log-opts="--all --full-history" --redact
gitleaks dir . --redact
git diff --cached --check
git diff --cached --stat
```

The secret-scan workflow scans fetched Git history on pushes and pull requests.
Automated scans cannot prove that every kind of private information is absent.
Also review filenames, commit author emails, documentation, screenshots, release
assets, and CI logs for personal data. Use a GitHub privacy-protected commit email.

If a credential is exposed, revoke or rotate it first. Removing a file from the
latest commit leaves earlier versions in history. Assess history cleanup and any
copies, logs, artifacts, or releases separately. Do not paste real credentials into
public issues or pull requests when reporting a problem.
