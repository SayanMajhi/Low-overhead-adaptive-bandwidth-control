#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 3 ]]; then
  echo "usage: bash scripts/component-timing/run.sh frozen.jar frozen-profile.json new-output-directory" >&2
  exit 2
fi

jar=$(realpath "$1")
profile=$(realpath "$2")
out=$3
mkdir "$out"
out=$(realpath "$out")
classpath="$out:$jar"
source_dir=$(dirname "$0")
javac --release 21 -cp "$jar" -d "$out" "$source_dir/ComponentTiming.java"
{
  sha256sum "$jar" "$profile" "$source_dir/ComponentTiming.java"
  java -version
  uname -sr
  /usr/bin/time --version | head -n 1
} > "$out/environment.txt" 2>&1

for run in 1-periodic 1-adaptive 2-adaptive 2-periodic 3-periodic 3-adaptive; do
  /usr/bin/time -v -o "$out/$run.process.txt" \
    java -Xms24m -Xmx192m -cp "$classpath" org.bandwidthcontrol.ComponentTiming "$profile" "${run#*-}" \
    > "$out/$run.components.csv"
done
