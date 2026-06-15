#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/promote_release_main.sh [--push] <version> [source_ref] [target_branch] [target_base]

Examples:
  scripts/promote_release_main.sh v0.8.0
  scripts/promote_release_main.sh --push v0.8.0 develop/dev main develop/main

Defaults:
  source_ref    = develop/dev
  target_branch = main
  target_base   = develop/main

This creates a single-parent release commit on target_branch whose tree matches
source_ref. It avoids squash-merge conflicts when dev and main have divergent
release histories, while preserving the dev -> main one-way release flow.
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_clean_tree() {
  git diff --quiet --ignore-submodules -- || die "working tree に未保存の変更があります"
  git diff --cached --quiet --ignore-submodules -- || die "index に staged 変更があります"

  if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    die "untracked files があります"
  fi
}

PUSH_TARGET=0
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --push)
      PUSH_TARGET=1
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL+=("$1")
        shift
      done
      ;;
    -*)
      die "unknown option: $1"
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

set -- "${POSITIONAL[@]}"

if [[ $# -lt 1 || $# -gt 4 ]]; then
  usage
  exit 1
fi

VERSION="$1"
SOURCE_REF="${2:-develop/dev}"
TARGET_BRANCH="${3:-main}"
TARGET_BASE="${4:-develop/main}"

require_clean_tree

git fetch develop
git rev-parse --verify --quiet "${SOURCE_REF}" >/dev/null || die "source ref が見つかりません: ${SOURCE_REF}"
git rev-parse --verify --quiet "${TARGET_BASE}" >/dev/null || die "target base が見つかりません: ${TARGET_BASE}"

git checkout -B "${TARGET_BRANCH}" "${TARGET_BASE}"

NEW_COMMIT="$(
  git commit-tree "${SOURCE_REF}^{tree}" \
    -p HEAD \
    -m "Release ${VERSION}"
)"

git update-ref "refs/heads/${TARGET_BRANCH}" "${NEW_COMMIT}"
git read-tree --reset -u HEAD

if ! git diff --quiet "${TARGET_BRANCH}" "${SOURCE_REF}"; then
  die "${TARGET_BRANCH} tree does not match ${SOURCE_REF}"
fi

if [[ "${PUSH_TARGET}" -eq 1 ]]; then
  git push develop "${TARGET_BRANCH}"
fi

cat <<EOF
Release commit created on '${TARGET_BRANCH}': ${NEW_COMMIT}
Tree source: ${SOURCE_REF}
Next:
  git push develop ${TARGET_BRANCH}
EOF
