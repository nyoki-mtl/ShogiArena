#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/export_public_snapshot.sh [--notes-file PATH] <version> [source_ref] [public_base]

Examples:
  scripts/export_public_snapshot.sh v0.8.0
  scripts/export_public_snapshot.sh --notes-file release-notes/v0.8.0.txt v0.8.0
  scripts/export_public_snapshot.sh v0.8.0 develop/main public/main
  # public/main が未作成（空リポ）でも実行可能

Defaults:
  source_ref  = develop/main
  public_base = public/main

This script:
  1) fetches develop/public remotes
  2) checks out export branch from public/main (or recreates it as orphan)
  3) overlays develop/main while excluding internal files
  4) removes excluded paths from snapshot
  5) creates a release snapshot commit

Notes:
  - 実行前に working tree / index / untracked files が空である必要があります。
  - --notes-file を指定すると、commit message の本文としてそのまま取り込みます。
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

build_commit_message() {
  local version="$1"
  local notes_file="$2"

  printf 'Release %s\n' "${version}"
  if [[ -n "${notes_file}" ]]; then
    printf '\n'
    cat "${notes_file}"
  fi
}

NOTES_FILE=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --notes-file)
      [[ $# -ge 2 ]] || die "--notes-file にはパスが必要です"
      NOTES_FILE="$2"
      shift 2
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

if [[ $# -lt 1 || $# -gt 3 ]]; then
  usage
  exit 1
fi

VERSION="$1"
SOURCE_REF="${2:-develop/main}"
PUBLIC_BASE="${3:-public/main}"
EXPORT_BRANCH="export-public"
PUBLIC_GIT_AUTHOR_NAME="${PUBLIC_GIT_AUTHOR_NAME:-nyoki-mtl}"
PUBLIC_GIT_AUTHOR_EMAIL="${PUBLIC_GIT_AUTHOR_EMAIL:-charmer.popopo@gmail.com}"

EXCLUDE_PATHS=(
  "AGENTS.md"
  "CLAUDE.md"
  "GEMINI.md"
  ".cursor/**"
  ".claude/**"
  "agent-docs/**"
  "_refs/**"
  ".sandbox/**"
  ".serena/**"
  "dist/**"
  "site/**"
  ".github/workflows/develop-ci.yml"
)

REMOVE_PATHS=(
  "AGENTS.md"
  "CLAUDE.md"
  "GEMINI.md"
  ".cursor"
  ".claude"
  "agent-docs"
  "dist"
  "site"
  ".github/workflows/develop-ci.yml"
)

[[ -z "${NOTES_FILE}" || -f "${NOTES_FILE}" ]] || die "notes file が見つかりません: ${NOTES_FILE}"

require_clean_tree

echo "[1/5] Fetch remotes"
git fetch develop
git fetch public

git rev-parse --verify --quiet "${SOURCE_REF}" >/dev/null || die "source ref が見つかりません: ${SOURCE_REF}"

HAS_PUBLIC_BASE=0
if git rev-parse --verify --quiet "${PUBLIC_BASE}" >/dev/null; then
  HAS_PUBLIC_BASE=1
fi

echo "[2/5] Prepare export branch"
if [[ "${HAS_PUBLIC_BASE}" -eq 1 ]]; then
  echo "  base: ${PUBLIC_BASE}"
  git checkout -B "${EXPORT_BRANCH}" "${PUBLIC_BASE}"
else
  echo "  base: (none, recreating orphan branch)"

  if git rev-parse --verify --quiet "refs/heads/${EXPORT_BRANCH}" >/dev/null; then
    if [[ "$(git branch --show-current)" == "${EXPORT_BRANCH}" ]]; then
      git checkout --detach HEAD
    fi
    git branch -D "${EXPORT_BRANCH}"
  fi

  git checkout --orphan "${EXPORT_BRANCH}"
  git rm -r --ignore-unmatch . >/dev/null 2>&1 || true
fi

echo "[3/5] Overlay ${SOURCE_REF} with excluded dev-only paths"
CHECKOUT_ARGS=("${SOURCE_REF}" -- .)
for path in "${EXCLUDE_PATHS[@]}"; do
  CHECKOUT_ARGS+=(":(exclude)${path}")
done
git checkout "${CHECKOUT_ARGS[@]}"

echo "[4/5] Remove excluded paths from public snapshot"
git rm -r --ignore-unmatch "${REMOVE_PATHS[@]}" >/dev/null 2>&1 || true
for path in "${REMOVE_PATHS[@]}"; do
  rm -rf -- "${path}"
done

echo "[5/5] Reflect deletions from ${SOURCE_REF}"
if [[ "${HAS_PUBLIC_BASE}" -eq 1 ]]; then
  while IFS= read -r path; do
    [[ -z "${path}" ]] && continue
    git rm --ignore-unmatch -- "${path}" >/dev/null 2>&1 || true
  done < <(git diff --name-only --diff-filter=D "${PUBLIC_BASE}..${SOURCE_REF}")
fi

git add -A

if git diff --cached --quiet; then
  echo "No changes to export."
  exit 0
fi

COMMIT_MESSAGE_FILE="$(mktemp)"
trap 'rm -f "${COMMIT_MESSAGE_FILE}"' EXIT
build_commit_message "${VERSION}" "${NOTES_FILE}" > "${COMMIT_MESSAGE_FILE}"

GIT_AUTHOR_NAME="${PUBLIC_GIT_AUTHOR_NAME}" \
GIT_AUTHOR_EMAIL="${PUBLIC_GIT_AUTHOR_EMAIL}" \
GIT_COMMITTER_NAME="${PUBLIC_GIT_AUTHOR_NAME}" \
GIT_COMMITTER_EMAIL="${PUBLIC_GIT_AUTHOR_EMAIL}" \
git commit -F "${COMMIT_MESSAGE_FILE}"

cat <<EOF
Snapshot commit created on branch '${EXPORT_BRANCH}'.
Next:
  git push public HEAD:main
EOF
