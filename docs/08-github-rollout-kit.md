# GitHub Rollout Kit

This rollout path is for public GitHub repositories only. Internal GitLab
repositories keep using `install.sh --platform gitlab` or their existing
GitLab include path.

## Inventory

The default GitHub inventory is `rollout.repos.yml`:

- `kevinwanghd/deliverhq`
- `kevinwanghd/UseGEO`
- `kevinwanghd/GoodNews_Globe`

Each repository uses the GitHub thin entrypoint. Target repositories receive a
small workflow caller, PR template, governance docs, and
`governance.config.yml`; AgentGate scripts stay centralized in
`kevinwanghd/AgentGate`.

## Preview

Run a dry preview first:

```bash
python scripts/rollout_github.py
```

Preview a single repository:

```bash
python scripts/rollout_github.py --repo UseGEO
```

## Apply Locally

Create local rollout branches without pushing:

```bash
python scripts/rollout_github.py --apply
```

The script refuses to proceed when a target repository has local changes.

## Push And Open PRs

After reviewing local changes:

```bash
python scripts/rollout_github.py --apply --push --pr
```

This creates one branch and one PR per repository. Do not push directly to
`main`.

Before any push, the rollout script now creates `.agentgate/mr-description.md`
inside the target branch and runs:

```bash
python scripts/agentgate.py pr verify --target-branch origin/<base>
```

The description manifest contains a hidden `agentgate-pr-bind` record with the
base ref, changed files, diff fingerprint, and the commit SHA that was current
when the manifest was prepared. The final commit SHA may differ after the
manifest is amended into the rollout commit, so the non-bypassable freshness
check is the diff fingerprint. If code changes after the manifest is prepared,
verification fails and the branch is not pushed.

`agentgate.py pr prepare` and `agentgate.py pr verify` require all code changes
to be committed first. The only allowed working-tree change during this step is
`.agentgate/mr-description.md` itself. This prevents a prepared description from
silently ignoring local edits that have not reached `HEAD` yet.

## GitLab Safety

The GitHub rollout inventory accepts only `platform: github`. If a GitLab
repository is added by mistake, the script exits before touching it. GitLab
repositories should stay on:

```bash
bash install.sh . --platform gitlab --mode pinned
```

That keeps private GitLab CI, runner compatibility, and protected-token setup
separate from GitHub Actions rollout.
