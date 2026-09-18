#!/usr/bin/env bash
# Fetch AMI distant-microphone audio and only_words RTTM references.
set -euo pipefail
out=''
subset=dev
mic=Array1-01
limit=''
force=false
usage() { echo 'Usage: fetch_ami.sh --out <dir> [--subset dev|test] [--mic Array1-01] [--limit <n>] [--force]'; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --force) force=true; shift ;;
    --out|--subset|--mic|--limit)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      case "$1" in
        --out) out=$2 ;; --subset) subset=$2 ;; --mic) mic=$2 ;; --limit) limit=$2 ;;
      esac
      shift 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[ -n "$out" ] || { usage >&2; exit 2; }
case "$subset" in dev|test) ;; *) echo '--subset must be dev or test' >&2; exit 2 ;; esac
[[ "$mic" =~ ^[A-Za-z0-9_-]+$ ]] || { echo 'Invalid microphone name' >&2; exit 2; }
if [ -n "$limit" ] && ! [[ "$limit" =~ ^[1-9][0-9]*$ ]]; then
  echo '--limit must be a positive integer' >&2; exit 2
fi
if command -v ffmpeg >/dev/null 2>&1; then
  converter=ffmpeg
elif command -v sox >/dev/null 2>&1; then
  converter=sox
else
  echo 'Audio conversion requires ffmpeg or sox; neither was found.' >&2
  exit 2
fi
command -v curl >/dev/null 2>&1 || { echo 'Download requires curl.' >&2; exit 2; }
mkdir -p "$out"
scratch=$(mktemp -d "${TMPDIR:-/tmp}/diarization-ami.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
setup=https://raw.githubusercontent.com/pyannote/AMI-diarization-setup/main
mirror=https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus
curl --fail --location --retry 3 "$setup/lists/$subset.meetings.txt" -o "$scratch/meetings.txt"
count=0
while IFS= read -r meeting || [ -n "$meeting" ]; do
  meeting=${meeting%$'\r'}
  [ -n "$meeting" ] || continue
  [[ "$meeting" == \#* ]] && continue
  [[ "$meeting" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "Invalid meeting ID: $meeting" >&2; exit 2; }
  if [ -n "$limit" ] && [ "$count" -ge "$limit" ]; then break; fi
  if [ "$force" = false ] && { [ -e "$out/$meeting.wav" ] || [ -L "$out/$meeting.wav" ]; }; then
    echo "Refusing to overwrite $out/$meeting.wav; use --force to replace it." >&2
    exit 2
  fi
  echo "Fetching $meeting ($mic, $subset)"
  curl --fail --location --retry 3 "$setup/only_words/rttms/$subset/$meeting.rttm" -o "$scratch/reference.rttm"
  curl --fail --location --retry 3 "$mirror/$meeting/audio/$meeting.$mic.wav" -o "$scratch/source.wav"
  if [ "$converter" = ffmpeg ]; then
    ffmpeg -nostdin -hide_banner -loglevel error -y -i "$scratch/source.wav" -ar 16000 -ac 1 -c:a pcm_s16le "$scratch/converted.wav"
  else
    sox "$scratch/source.wav" -r 16000 -c 1 -b 16 -e signed-integer "$scratch/converted.wav"
  fi
  if [ "$force" = true ]; then
    mv -f "$scratch/converted.wav" "$out/$meeting.wav"
  else
    mv -n "$scratch/converted.wav" "$out/$meeting.wav"
    if [ -e "$scratch/converted.wav" ]; then
      echo "Refusing to overwrite $out/$meeting.wav; use --force to replace it." >&2
      exit 2
    fi
  fi
  mv "$scratch/reference.rttm" "$out/$meeting.rttm"
  count=$((count + 1))
done < "$scratch/meetings.txt"
echo "Fetched $count fixture pairs into $out"
