#!/usr/bin/env bash
set -euo pipefail
: "${RUNNER_TEMP:?}" "${GITHUB_PATH:?}"
cli="$RUNNER_TEMP/wokwi-cli"
curl --fail --silent --show-error --location --retry 3 -o "$cli" https://github.com/wokwi/wokwi-cli/releases/download/v0.29.1/wokwi-cli-linuxstatic-x64
echo "3c9d6da007d008a3822ab468318037285440055132c59fd659d7306e40f14258  $cli" | sha256sum --check
chmod +x "$cli"
echo "$RUNNER_TEMP" >> "$GITHUB_PATH"
"$cli" --version
