#!/usr/bin/env bash
#
# a9-assets.sh — the asset catalogue (data/assets.tsv) against ~/a9-assets.
# Ported from A9Toolkit's `cmd/assets` (2026-08-13), remapped to the current
# ~/a9-assets layout.
#
#   a9-assets.sh [status]        what is present / missing (default, READ-ONLY).
#                                Sizes are checked against data/assets.lock.
#   a9-assets.sh verify          same, plus full sha256 of every file in the lock
#   a9-assets.sh fetch <id|all>  download into $A9_DATA (gdown / curl). Never
#                                overwrites anything that already exists.
#   a9-assets.sh import <dir>    recognise known assets in a folder and copy them
#                                into $A9_DATA (never overwrites)
#   a9-assets.sh path <id>       print where an asset lives
#
# $A9_DATA is a symlink to an NFS share that rejects temp-then-rename and
# dot-temp files: downloads go to local scratch first, then are copied to the
# FINAL filename directly with `cp -n`. Nothing is ever renamed or deleted.
# gdown comes from PATH or `nix shell nixpkgs#gdown`.
#
# Exit: status/verify return 1 if a required asset is missing or a size/sha256
# disagrees with the lock; 0 otherwise.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

ASSET_TSV="$A9_HOME/data/assets.tsv"
LOCKFILE="$A9_HOME/data/assets.lock"
SCRATCH_ROOT="${A9_SCRATCH:-${TMPDIR:-/tmp}}"

if [ -t 1 ]; then G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; D=$'\e[2m'; N=$'\e[0m'; else G=; R=; Y=; D=; N=; fi
say()  { printf '%s\n' "$*"; }
info() { printf '%s\n' "$*"; }
warn() { printf '%swarn:%s %s\n' "$Y" "$N" "$*" >&2; }
err()  { printf '%serror:%s %s\n' "$R" "$N" "$*" >&2; }
die()  { err "$*"; exit 1; }

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1 B"; }

[ -f "$ASSET_TSV" ] || die "catalogue not found: $ASSET_TSV"

usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; }

# ---- catalogue helpers ------------------------------------------------------
asset_row() { awk -F'\t' -v i="$1" '/^#/ || NF==0 { next } $1 == i { print; exit }' "$ASSET_TSV"; }
asset_path() {
  local row; row=$(asset_row "$1"); [ -n "$row" ] || return 1
  printf '%s/%s' "$A9_DATA" "$(cut -f2 <<<"$row")"
}
lock_field() { # lock_field <id> <col 2=sha 3=bytes>
  [ -f "$LOCKFILE" ] || return 0
  awk -F'\t' -v i="$1" -v c="$2" '/^#/ || NF==0 { next } $1 == i { print $c; exit }' "$LOCKFILE"
}
# present: a file exists, or a directory has at least one entry. A9_DATA is a
# symlink, so every find below goes through "$path/" (trailing slash).
asset_have() {
  local p="$1"
  if [ -d "$p" ]; then [ -n "$(find "$p/" -mindepth 1 -print -quit 2>/dev/null)" ]
  else [ -f "$p" ]; fi
}
# extra completeness check for directory assets, by id
dir_marker_problem() {
  case "$1" in
    stock-rom)
      local d="$2" m
      for m in rawprogram0_001360E1.xml prog_firehose_ddr_001360E1.elf super_1.img; do
        [ -e "$d/$m" ] || { echo "no $m"; return; }
      done ;;
    gsi)
      [ -n "$(find "$2/" -maxdepth 1 \( -name '*.img' -o -name '*.img.xz' \) -print -quit 2>/dev/null)" ] \
        || echo "no .img files" ;;
  esac
}

# ---- status / verify --------------------------------------------------------
cmd_status() {
  local deep="$1" missing_req=0 bad=0 n_present=0 n_missing=0
  [ -d "$A9_DATA/" ] || die "A9_DATA not reachable: $A9_DATA"
  printf '%-22s %-4s %-9s %s\n' ID REQ STATUS DETAIL
  while IFS=$'\t' read -r id path req kind source note; do
    case "$id" in ''|\#*) continue ;; esac
    local full="$A9_DATA/$path" status detail="" lsz lsha sz sha prob
    if asset_have "$full"; then
      n_present=$((n_present + 1)); status="present"
      if [ -d "$full" ]; then
        detail="$(find "$full/" -type f 2>/dev/null | wc -l) files, $(du -sh "$full/" 2>/dev/null | cut -f1)"
        prob=$(dir_marker_problem "$id" "$full")
        [ -n "$prob" ] && { status="INCOMPLETE"; detail="$detail; $prob"; bad=$((bad + 1)); }
      else
        sz=$(stat -c %s "$full"); detail="$(human "$sz")"
        lsz=$(lock_field "$id" 3); lsha=$(lock_field "$id" 2)
        if [ -n "$lsz" ] && [ "$sz" != "$lsz" ]; then
          status="BAD-SIZE"; detail="$detail (lock says $lsz bytes)"; bad=$((bad + 1))
        elif [ "$deep" = 1 ] && [ -n "$lsha" ]; then
          sha=$(sha256sum "$full" | cut -d' ' -f1)
          if [ "$sha" = "$lsha" ]; then detail="$detail, sha256 ok"
          else status="BAD-SHA"; detail="$detail, sha256 ${sha:0:12} != lock ${lsha:0:12}"; bad=$((bad + 1)); fi
        elif [ -n "$lsz" ]; then detail="$detail, size ok"
        else detail="$detail, not in lock"; fi
      fi
    else
      n_missing=$((n_missing + 1)); status="missing"
      detail="$kind: $note"
      if [ "$req" = yes ]; then status="MISSING"; missing_req=$((missing_req + 1)); fi
    fi
    local colour=$G
    case "$status" in present) colour=$G ;; missing) colour=$D ;; *) colour=$R ;; esac
    printf '%-22s %-4s %s%-9s%s %s\n' "$id" "$req" "$colour" "$status" "$N" "$detail"
  done < "$ASSET_TSV"
  say ""
  say "$n_present present, $n_missing missing ($missing_req required), $bad failed checks   [$A9_DATA]"
  [ "$missing_req" -eq 0 ] && [ "$bad" -eq 0 ]
}

# ---- fetch ------------------------------------------------------------------
run_gdown() {
  if command -v gdown >/dev/null 2>&1; then gdown "$@"
  else nix shell nixpkgs#gdown -c gdown "$@"; fi
}

# Copy a file to its final name, never overwriting. NFS-safe: no rename/chmod.
put_file() { # put_file <src> <dest>
  [ -e "$2" ] && { warn "exists, not overwriting: $2"; return 0; }
  mkdir -p "$(dirname "$2")" && cp --no-preserve=all "$1" "$2"
}
put_tree() { # put_tree <srcdir> <destdir>  (cp -n: never overwrites)
  mkdir -p "$2" && cp -rn --no-preserve=all "$1"/. "$2"/
}

check_lock() { # check_lock <id> <file>
  local lsha sha; lsha=$(lock_field "$1" 2)
  [ -n "$lsha" ] || { warn "$1: no checksum in the lock, not verified"; return 0; }
  sha=$(sha256sum "$2" | cut -d' ' -f1)
  [ "$sha" = "$lsha" ] || { err "$1: sha256 mismatch (got ${sha:0:16}, lock ${lsha:0:16})"; return 1; }
  say "  $1: sha256 verified"
}

fetch_one() {
  local id="$1" row path req kind source note full tmp
  row=$(asset_row "$id"); [ -n "$row" ] || die "unknown asset: $id"
  IFS=$'\t' read -r _ path req kind source note <<<"$row"
  full="$A9_DATA/$path"
  if asset_have "$full"; then say "$id: already present ($path), not touching it"; return 0; fi

  case "$kind" in
    import|derived)
      err "$id has no public download ($kind): $source"
      [ "$kind" = import ] && err "  copy it in with: $0 import <dir>"
      return 1 ;;
  esac
  say "fetching $id: $note"
  tmp=$(mktemp -d "$SCRATCH_ROOT/a9-assets.XXXXXX") || die "no scratch dir"
  case "$kind" in
    gdrive-file)
      run_gdown "https://drive.google.com/uc?id=$source" -O "$tmp/$(basename "$path")" \
        || { rm -rf "$tmp"; die "download failed for $id (Drive rate limits: retry later)"; }
      check_lock "$id" "$tmp/$(basename "$path")" || { rm -rf "$tmp"; return 1; }
      put_file "$tmp/$(basename "$path")" "$full" ;;
    gdrive-folder)
      mkdir -p "$tmp/dl"
      run_gdown --folder "https://drive.google.com/drive/folders/$source" -O "$tmp/dl" \
        || { rm -rf "$tmp"; die "download failed for $id"; }
      # gdown nests the Drive folder's own name inside -O; flatten it.
      local dl="$tmp/dl"
      if [ "$(find "$dl" -mindepth 1 -maxdepth 1 | wc -l)" = 1 ] && [ -d "$(find "$dl" -mindepth 1 -maxdepth 1)" ]; then
        dl=$(find "$dl" -mindepth 1 -maxdepth 1)
      fi
      if [ "$id" = fastboot-hisense ]; then
        local zip fb; zip=$(find "$dl" -iname 'linux*.zip' -print -quit)
        [ -n "$zip" ] || { rm -rf "$tmp"; die "no linux-x86.zip in the Drive folder"; }
        mkdir -p "$tmp/x"
        extract_zip "$zip" "$tmp/x" || { rm -rf "$tmp"; die "could not unzip $zip"; }
        fb=$(find "$tmp/x" -name fastboot -type f -print -quit)
        [ -n "$fb" ] || { rm -rf "$tmp"; die "no fastboot binary in $(basename "$zip")"; }
        check_lock "$id" "$fb" || { rm -rf "$tmp"; return 1; }
        put_file "$fb" "$full"
        chmod +x "$full" 2>/dev/null || warn "chmod +x refused (NFS); run it via 'bash -c' or copy it to /tmp"
      else
        put_tree "$dl" "$full"
      fi ;;
    url)
      command -v curl >/dev/null || { rm -rf "$tmp"; die "curl not found"; }
      curl -fL --progress-bar "$source" -o "$tmp/dl.bin" || { rm -rf "$tmp"; die "download failed for $id"; }
      check_lock "$id" "$tmp/dl.bin" || { rm -rf "$tmp"; return 1; }
      put_file "$tmp/dl.bin" "$full" ;;
    *) rm -rf "$tmp"; die "unknown kind '$kind' for $id" ;;
  esac
  rm -rf "$tmp"
  asset_have "$full" && say "$id -> $path" || die "$id: nothing landed at $full"
}

extract_zip() { # extract_zip <zip> <dir>
  if command -v unzip >/dev/null 2>&1; then unzip -q -o "$1" -d "$2"
  elif command -v 7z >/dev/null 2>&1; then 7z x -y -o"$2" "$1" >/dev/null
  else python3 -I -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$1" "$2"; fi
}

cmd_fetch() {
  local target="${1:-}"; [ -n "$target" ] || { usage; exit 2; }
  if [ "$target" = all ]; then
    local failed="" id kind
    while IFS=$'\t' read -r id _ _ kind _; do
      case "$id" in ''|\#*) continue ;; esac
      case "$kind" in import|derived) continue ;; esac
      ( fetch_one "$id" ) || failed="$failed $id"
    done < "$ASSET_TSV"
    [ -z "$failed" ] || { warn "could not fetch:$failed"; return 1; }
    say "all downloadable assets present"
  else
    fetch_one "$target"
  fi
}

# ---- import -----------------------------------------------------------------
cmd_import() {
  local src="${1:-}"; [ -n "$src" ] || { usage; exit 2; }
  [ -d "$src" ] || die "not a directory: $src"
  local found=0 skipped=0 f to
  say "scanning $src"

  find_in() { find "$src/" -iname "$1" ${2:+-type "$2"} -print -quit 2>/dev/null; }
  newest() { find "$src/" "$@" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-; }
  copy_to() { # copy_to <id> <srcfile>
    to=$(asset_path "$1")
    if [ -e "$to" ]; then say "  $1: already present, skipping"; skipped=$((skipped + 1)); return 0; fi
    say "  $1  <- ${2#"$src"/}"
    put_file "$2" "$to" && found=$((found + 1))
  }
  have() { asset_have "$(asset_path "$1")"; }

  if ! have firehose; then
    f=$(find_in prog_firehose_ddr_001360E1.elf f)
    [ -n "$f" ] && copy_to firehose "$f"
  fi
  if ! have stock-rom; then
    f=$(find_in 'rawprogram0*.xml' f)
    if [ -n "$f" ]; then
      say "  stock-rom  <- ${f%/*}"; put_tree "$(dirname "$f")" "$(asset_path stock-rom)" && found=$((found + 1))
    fi
  else skipped=$((skipped + 1)); fi
  if ! have fastboot-hisense; then
    f=$(find_in linux-x86.zip f)
    if [ -n "$f" ]; then
      local x; x=$(mktemp -d "$SCRATCH_ROOT/a9-assets.XXXXXX")
      extract_zip "$f" "$x" && fb=$(find "$x" -name fastboot -type f -print -quit)
      [ -n "${fb:-}" ] && { copy_to fastboot-hisense "$fb"; chmod +x "$(asset_path fastboot-hisense)" 2>/dev/null; }
      rm -rf "$x"
    else
      f=$(find "$src/" -name fastboot -type f -print -quit 2>/dev/null)
      [ -n "$f" ] && head -c4 "$f" | grep -q ELF && copy_to fastboot-hisense "$f"
    fi
  fi
  if ! have twrp; then
    f=$(newest \( -iname 'twrp*.img' -o -iname 'recovery.img' \) -type f -size +10M)
    [ -n "$f" ] && copy_to twrp "$f"
  fi
  if ! have librecovery-ui; then f=$(find_in librecovery_ui.so f); [ -n "$f" ] && copy_to librecovery-ui "$f"; fi
  if ! have magisk; then f=$(find_in 'Magisk-v30.7.apk' f); [ -n "$f" ] && copy_to magisk "$f"; fi
  if ! have boot-magisk-patched; then f=$(find_in 'magisk_patched-30700*.img' f); [ -n "$f" ] && copy_to boot-magisk-patched "$f"; fi
  if ! have qcn; then
    f=$(find_in '*.qcn' f)
    if [ -n "$f" ]; then copy_to qcn "$f"; warn "the QCN contains this handset's IMEI: never commit or share it"; fi
  fi
  if ! have aod-cat; then f=$(find_in eink-cat.png f); [ -n "$f" ] && copy_to aod-cat "$f"; fi
  # GSIs: a set, not one canonical file
  local gdir; gdir=$(asset_path gsi)
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ -e "$gdir/$(basename "$f")" ]; then skipped=$((skipped + 1)); continue; fi
    say "  gsi  <- $(basename "$f") ($(human "$(stat -c %s "$f")"))"
    put_file "$f" "$gdir/$(basename "$f")" && found=$((found + 1))
  done < <(find "$src/" \( -iname 'lineage-*.img*' -o -iname 'aosp*.img' -o -iname '*gsi*.img' \) -type f -size +500M 2>/dev/null)

  say "imported $found, skipped $skipped already present"
  [ "$found" -eq 0 ] && [ "$skipped" -eq 0 ] && warn "nothing recognised under $src"
  say "next: $0 verify"
  return 0
}

case "${1:-status}" in
  status)  cmd_status 0 ;;
  verify)  cmd_status 1 ;;
  fetch)   shift; cmd_fetch "$@" ;;
  import)  shift; cmd_import "$@" ;;
  path)    shift; asset_path "${1:?asset id required}"; echo ;;
  -h|--help|help) usage ;;
  *) err "unknown subcommand: $1"; usage; exit 2 ;;
esac
