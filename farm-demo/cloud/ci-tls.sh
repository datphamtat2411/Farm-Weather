#!/usr/bin/env bash
# Disposable CA and server certificate for loopback CI only.
set -euo pipefail
dir="${1:?output directory required}"
umask 077
mkdir -p "$dir"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj '/CN=Farm CI CA' \
  -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' \
  -keyout "$dir/ca.key" -out "$dir/ca.crt" 2>/dev/null
openssl req -new -newkey rsa:2048 -nodes -subj '/CN=localhost' \
  -keyout "$dir/server.key" -out "$dir/server.csr" 2>/dev/null
printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\n' > "$dir/extensions"
openssl x509 -req -in "$dir/server.csr" -CA "$dir/ca.crt" -CAkey "$dir/ca.key" \
  -CAcreateserial -days 2 -extfile "$dir/extensions" -out "$dir/server.crt" 2>/dev/null
rm "$dir/ca.key" "$dir/server.csr" "$dir/extensions" "$dir/ca.srl"
# Container runs as an unprivileged user; this key is disposable and only mounted in isolated CI.
chmod 755 "$dir"
chmod 644 "$dir/server.crt" "$dir/server.key" "$dir/ca.crt"
