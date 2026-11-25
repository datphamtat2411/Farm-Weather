#!/usr/bin/env bash
set -euo pipefail
source_dir="${1:?Firmware artifact directory required}"
mkdir -p farm-demo/evidence farm-demo/firmware/.pio/build/bluepill_f103c8
git rev-parse HEAD > farm-demo/evidence/commit.txt
cmp farm-demo/evidence/commit.txt "$source_dir/commit.txt"
(cd "$source_dir" && sha256sum --check firmware-sha256.txt)
cp "$source_dir"/firmware.{bin,elf} farm-demo/firmware/.pio/build/bluepill_f103c8/
cp "$source_dir/firmware-sha256.txt" farm-demo/evidence/
