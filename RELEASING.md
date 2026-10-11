# Releasing

How maintainers publish a new version of `evalforge-agents` to PyPI.

Releases run from `.github/workflows/release.yml` using PyPI Trusted
Publishing: GitHub proves the upload came from this repository's workflow, so
no PyPI API token is stored anywhere.

## Release a new version

1. Bump `version` in `pyproject.toml` and commit it to `main`.
2. Wait for CI on `main` to pass.
3. Push a matching tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

4. Open the workflow run in the repository's **Actions** tab and approve the
   deployment to the `pypi` environment (**Review deployments** → tick
   `pypi` → **Approve and deploy**).
5. Check that the release appears at https://pypi.org/project/evalforge-agents/.

## What the workflow does

| Job | Steps |
|---|---|
| `build` | Rejects a tag that doesn't match `pyproject.toml`'s version; runs ruff, mypy and pytest; builds the sdist and wheel; runs `twine check --strict` |
| `publish` | Waits for approval in the `pypi` environment, then uploads the build with Trusted Publishing |

Nothing is uploaded unless every build step passes and the deployment is
approved.

## Rules

- **A version can be published only once.** PyPI never accepts the same
  version again, even after it's deleted. A fix always needs a new version.
- **If the build fails, nothing was published.** Fix the problem on `main`.
  Because that version was never uploaded, you can delete the tag and push
  it again on the fixed commit:

  ```bash
  git push origin :refs/tags/vX.Y.Z
  git tag -d vX.Y.Z
  git tag vX.Y.Z <fixed-commit>
  git push origin vX.Y.Z
  ```

  Never move a tag for a version that is already on PyPI; bump the version
  instead.
- The PyPI project page shows the README from the released commit, so README
  changes appear there with the next release.

## One-time setup

Already done for this repository. Repeat it only for a fork or a new PyPI
project.

1. On PyPI: **Your account** → **Publishing** → **Add a new pending
   publisher**, with project `evalforge-agents`, owner `AbhilashSomigari`,
   repository `evalforge`, workflow `release.yml`, and environment `pypi`.
2. On GitHub: **Settings** → **Environments** → `pypi` → add the release
   approvers as required reviewers.
