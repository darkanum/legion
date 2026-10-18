# Contributing to Legion

Thank you for helping. Legion runs agents that write to people's repositories, so a change is judged first on
whether it keeps the host and the team safe, then on whether it is simple.

## Before you start

- Read [AGENTS.md](AGENTS.md). It holds the rules every change follows: the folder rule, no polling, the write token
  stays on the host, one setting in one place, nothing that names a company.
- For a new feature or a new agent, open an issue first and describe the problem. A short discussion saves a long
  review.
- For a security problem, do not open an issue. Follow [SECURITY.md](SECURITY.md).

## Making a change

1. Fork the repository and branch from `main`, with a prefix that says what the change is: `fix/`, `feat/`, `docs/`,
   `refactor/`, `test/`, `chore/`.
2. Keep the change to one concern. A refactor the change needs is fine; an unrelated clean-up is its own PR.
3. Add or update the `unittest` tests next to the code you change. They need no install:

   ```sh
   for folder in shared/lib shared/sandbox shared/hooks/host marbas/host valac/host orobas/host; do
     (cd "$folder" && python3 -m unittest)
   done
   ```

4. Update the docs in [docs/](docs/) and `legion.env.example` when you change behaviour or add a setting.
5. Open a pull request with a conventional title (`fix: ...`, `feat(orobas): ...`), say what changes and why, and
   link the issue with `Closes #N`.

## Style

- Python 3.11, the standard library on the host where it is enough. A new dependency needs a reason in the PR.
- Bash with `set -euo pipefail` for scripts. Quote every variable.
- Comments say why, not what. Docs and messages are plain English, short sentences, no hype.
- No AI attribution lines in commits or PR bodies.

## License

By contributing, you agree that your contribution is licensed under the [Apache License 2.0](LICENSE), the license
of this project.
