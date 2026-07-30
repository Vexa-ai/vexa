#!/usr/bin/env bash
# ALLOY: single owner for the Git source identity stamped into local Lite images.
set -euo pipefail

root=""
format="env"

while (($#)); do
  case "$1" in
    --root)
      [[ $# -ge 2 ]] || { echo "[ALLOY] source-identity: --root requires a path" >&2; exit 2; }
      root="$2"
      shift 2
      ;;
    --format)
      [[ $# -ge 2 ]] || { echo "[ALLOY] source-identity: --format requires env or json" >&2; exit 2; }
      format="$2"
      shift 2
      ;;
    *)
      echo "[ALLOY] source-identity: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$format" in
  env|json) ;;
  *) echo "[ALLOY] source-identity: --format must be env or json" >&2; exit 2 ;;
esac

if [[ -z "$root" ]]; then
  root="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || { echo "[ALLOY] source-identity: not inside a Git worktree" >&2; exit 2; }
fi

root="$(cd "$root" && pwd -P)"
# ALLOY: an explicit root owns repository routing; inherited gate or caller Git variables must not
# redirect identity reads to another checkout or index.
git_command=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE git -C "$root")
if [[ -f "$root/.git" ]]; then
  gitdir_pointer="$(sed -n 's/^gitdir: //p' "$root/.git" | head -n 1)"
  if [[ "$gitdir_pointer" =~ ^([A-Za-z]):[/\\](.*)$ ]]; then
    drive="${BASH_REMATCH[1],,}"
    tail="${BASH_REMATCH[2]//\\//}"
    translated_gitdir="/mnt/$drive/$tail"
    if [[ "$root" =~ ^/mnt/[A-Za-z]/ && -d "$translated_gitdir" ]]; then
      git_command=(
        env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE
        git "--git-dir=$translated_gitdir" "--work-tree=$root"
      )
    fi
  fi
fi

"${git_command[@]}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "[ALLOY] source-identity: $root is not a Git worktree" >&2; exit 2; }

revision="$("${git_command[@]}" rev-parse HEAD)"
dirty=false

cd "$root"
mapfile -d '' -t source_paths \
  < <("${git_command[@]}" ls-files -co --exclude-standard -z | LC_ALL=C sort -z)
declare -A index_modes=() index_contents=() changed_tracked=()
while IFS= read -r -d '' record; do
  metadata="${record%%$'\t'*}"
  path="${record#*$'\t'}"
  read -r mode content stage <<<"$metadata"
  [[ "$stage" == "0" ]] \
    || { echo "[ALLOY] source-identity: unmerged index entry: $path" >&2; exit 2; }
  index_modes["$path"]="$mode"
  index_contents["$path"]="$content"
done < <("${git_command[@]}" ls-files -s -z)
while IFS= read -r -d '' path; do
  changed_tracked["$path"]=1
  dirty=true
# ALLOY: porcelain diff verifies bytes/modes; diff-files --name-only can expose only a
# cross-Git stat-cache mismatch after Windows Git touches an index consumed from WSL.
done < <("${git_command[@]}" diff --no-ext-diff --name-only -z --)
if ! "${git_command[@]}" diff-index --cached --quiet HEAD --; then
  dirty=true
fi

declare -a source_modes=() source_contents=() regular_paths=() regular_indices=()
for index in "${!source_paths[@]}"; do
  path="${source_paths[$index]}"
  mode="${index_modes[$path]-}"
  [[ -n "$mode" ]] || dirty=true
  if [[ -z "$mode" || -n "${changed_tracked[$path]-}" ]]; then
    if [[ -L "$path" ]]; then
      mode="120000"
    elif [[ -f "$path" ]]; then
      if [[ -x "$path" ]]; then mode="100755"; else mode="100644"; fi
    else
      mode="missing"
    fi
  fi
  source_modes[$index]="$mode"

  if [[ "$mode" == "120000" ]]; then
    source_contents[$index]="$(readlink "$path" | "${git_command[@]}" hash-object --stdin)"
  elif [[ "$mode" == "160000" ]]; then
    source_contents[$index]="${index_contents[$path]}"
  elif [[ "$mode" == "100644" || "$mode" == "100755" ]]; then
    regular_indices+=("$index")
    regular_paths+=("$path")
  else
    source_contents[$index]="missing"
  fi
done

# ALLOY: hash ordinary files in bounded batches; one Git process per file is prohibitively
# slow for Windows-mounted WSL worktrees.
batch_size=256
for ((offset = 0; offset < ${#regular_paths[@]}; offset += batch_size)); do
  batch=("${regular_paths[@]:offset:batch_size}")
  mapfile -t batch_hashes < <("${git_command[@]}" hash-object --no-filters -- "${batch[@]}")
  [[ ${#batch_hashes[@]} -eq ${#batch[@]} ]] \
    || { echo "[ALLOY] source-identity: Git returned an incomplete hash batch" >&2; exit 2; }
  for relative in "${!batch[@]}"; do
    source_contents[${regular_indices[$((offset + relative))]}]="${batch_hashes[$relative]}"
  done
done

fingerprint="$(
  {
    for index in "${!source_paths[@]}"; do
      printf '%s\0%s\0%s\0' \
        "${source_paths[$index]}" "${source_modes[$index]}" "${source_contents[$index]}"
    done
  } | sha256sum | awk '{print $1}'
)"

if [[ "$format" == "json" ]]; then
  printf '{"revision":"%s","dirty":%s,"fingerprint":"%s"}\n' \
    "$revision" "$dirty" "$fingerprint"
else
  printf 'SOURCE_REVISION=%s\n' "$revision"
  printf 'SOURCE_DIRTY=%s\n' "$([[ "$dirty" == true ]] && printf 1 || printf 0)"
  printf 'SOURCE_FINGERPRINT=%s\n' "$fingerprint"
fi
