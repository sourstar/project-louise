#!/bin/sh
# Converts a Hugging Face model to GGUF with the llama.cpp OF AN ENGINE SERIES: the converter the engine's own tree
# carries (its patches' fixes included - a BERT classifier's pooler, a declared GELU, a tokenizer's hash), so a GGUF
# is reproducible from the series and the model's files alone (docs/notes/medcpt-on-llama-cpp.md §6).
#
#   sh deploy/localai/llama-cpp/convert.sh <series file> <hf dir> <out.gguf> [convert_hf_to_gguf.py options ...]
#   sh deploy/localai/llama-cpp/convert.sh deploy/localai/patches/staged/engine-guard.series \
#       /mnt/ssd5/apps/models/medcpt/models--ncbi--MedCPT-Article-Encoder MedCPT-Article-Encoder-f16.gguf --outtype f16
#
# THE TREE, as build.sh and LocalAI's backend make it: LocalAI at the series' `base`, `git am` of each patch of the
# series in order; then llama.cpp at the LLAMA_VERSION of that tree's backend/cpp/llama-cpp/Makefile, and every file
# of its backend/cpp/llama-cpp/patches/ applied in `ls` order with `patch -p1`, as its prepare.sh applies them.
# NAME_ONLY=1 prints the series' engine name (build.sh's) and stops; STAGE_ONLY=1 lists the input it would convert.
#
# THE INPUT, fixed so that the bytes do not depend on where the model sits: <hf dir> is a snapshot (it holds
# config.json) or a Hugging Face cache directory (models--<org>--<name>) holding ONE snapshot. Its files are copied
# (links resolved) into a directory named after the repository (<name>, or MODEL_NAME=...), because the converter
# writes that name into the GGUF; README.md (a model card the converter would read as metadata), licences and
# .gitattributes are left out, and pytorch_model*.bin where *.safetensors exist.
#
# THE CONVERTER runs in python:3.12-slim with the tree's own requirements-convert_hf_to_gguf.txt installed (docker
# and the network to PyPI), or with CONVERT_PYTHON=<python> whose environment already holds them.
#
# Out: <out.gguf>, and <out.gguf>.lock beside it: the series' name, LocalAI's base, llama.cpp's version, the model's
# name, every input file's sha256, the converter's options, the GGUF's sha256 - what a second conversion must match.
set -eu
SERIES_FILE=${1:?series file}
HF=${2:?hf dir: a snapshot, or a models--<org>--<name> cache directory}
OUT=${3:?out.gguf}
shift 3
HERE=$(cd "$(dirname "$0")" && pwd)
PATCHES=$HERE/../patches
# the series, read as build.sh reads it (and named as it names it: tests/test_localai_convert.py holds the two equal)
[ -r "$SERIES_FILE" ] || { echo "series file not readable: $SERIES_FILE" >&2; exit 2; }
SERIES_FILE=$(cd "$(dirname "$SERIES_FILE")" && pwd)/$(basename "$SERIES_FILE")
BASE=$(sed -n 's/^base //p' "$SERIES_FILE")
SERIES=$(grep -v '^#' "$SERIES_FILE" | grep -v '^base ' | grep .)
for p in $SERIES; do
  [ -r "$PATCHES/$p" ] || { echo "patch not readable: $PATCHES/$p" >&2; exit 2; }
done
HEAD=$(cd "$PATCHES" && cat "$SERIES_FILE" $SERIES | sha256sum | cut -c1-12)
if [ -n "${NAME_ONLY:-}" ]; then
  echo "$HEAD"; exit 0
fi

# the model's snapshot, and the repository's name
if [ -f "$HF/config.json" ]; then
  SNAP=$HF
else
  n=$(find "$HF/snapshots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
  [ "$n" = 1 ] || { echo "$HF: neither a snapshot (no config.json) nor a cache directory with one snapshot ($n)" >&2; exit 2; }
  SNAP=$(find "$HF/snapshots" -mindepth 1 -maxdepth 1 -type d)
fi
case $(basename "$(cd "$HF" && pwd)") in
  models--*--*) DERIVED=$(basename "$(cd "$HF" && pwd)" | sed 's/^models--[^-]*--//') ;;
  *)            DERIVED=$(basename "$(cd "$SNAP" && pwd)") ;;
esac
MODEL_NAME=${MODEL_NAME:-$DERIVED}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# the input under the repository's name
IN=$WORK/in/$MODEL_NAME
mkdir -p "$IN"
has_safetensors=$(find "$SNAP" -maxdepth 1 -name '*.safetensors' | head -1)
for f in "$SNAP"/* "$SNAP"/.[!.]*; do
  [ -e "$f" ] || continue
  b=$(basename "$f")
  case $b in
    README.md|LICENSE|LICENSE.*|.gitattributes) continue ;;
    pytorch_model*.bin) [ -n "$has_safetensors" ] && continue ;;
  esac
  [ -f "$f" ] && cp -L "$f" "$IN/$b"
done

# STAGE_ONLY=1: say what would be converted, under which name, and stop (nothing fetched)
if [ -n "${STAGE_ONLY:-}" ]; then
  echo "model_name $MODEL_NAME"
  (cd "$IN" && ls -A)
  exit 0
fi

git -C "$WORK" init -q
git -C "$WORK" -c core.autocrlf=false fetch -q --depth 1 https://github.com/mudler/LocalAI.git "$BASE"
git -C "$WORK" -c core.autocrlf=false checkout -q FETCH_HEAD
for p in $SERIES; do
  git -C "$WORK" -c user.name=build -c user.email=build@localhost -c core.autocrlf=false am -q "$PATCHES/$p"
done
LL=$WORK/backend/cpp/llama-cpp
LLAMA_VERSION=$(sed -n 's/^LLAMA_VERSION?=//p' "$LL/Makefile")
LLAMA_REPO=$(sed -n 's/^LLAMA_REPO?=//p' "$LL/Makefile")
[ -n "$LLAMA_VERSION" ] && [ -n "$LLAMA_REPO" ] || { echo "no LLAMA_VERSION or LLAMA_REPO in $LL/Makefile" >&2; exit 2; }
mkdir "$WORK/llama.cpp"
git -C "$WORK/llama.cpp" init -q
git -C "$WORK/llama.cpp" -c core.autocrlf=false fetch -q --depth 1 "$LLAMA_REPO" "$LLAMA_VERSION"
git -C "$WORK/llama.cpp" -c core.autocrlf=false checkout -q FETCH_HEAD
for p in $(ls "$LL/patches"); do
  patch -s -d "$WORK/llama.cpp" -p1 < "$LL/patches/$p"
done

if [ -n "${CONVERT_PYTHON:-}" ]; then
  PYTHONPATH=$WORK/llama.cpp/gguf-py "$CONVERT_PYTHON" "$WORK/llama.cpp/convert_hf_to_gguf.py" "$IN" \
    --outfile "$WORK/out.gguf" "$@"
else
  docker run --rm -v "$WORK:/w" python:3.12-slim sh -c \
    'pip install -q -r /w/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt &&
     PYTHONPATH=/w/llama.cpp/gguf-py exec python /w/llama.cpp/convert_hf_to_gguf.py "/w/in/$0" --outfile /w/out.gguf "$@"' \
    "$MODEL_NAME" "$@"
fi
cp "$WORK/out.gguf" "$OUT"
SUM=$(sha256sum "$OUT" | cut -d' ' -f1)
{
  echo "series $HEAD $(basename "$SERIES_FILE")"
  echo "localai_base $BASE"
  echo "llama_cpp $LLAMA_VERSION"
  echo "model_name $MODEL_NAME"
  (cd "$IN" && sha256sum * | sed 's/^/input /')
  echo "options $*"
  echo "gguf $SUM"
} > "$OUT.lock"
echo "$SUM  $OUT"
