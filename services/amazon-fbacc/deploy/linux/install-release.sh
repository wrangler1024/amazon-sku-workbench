#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" != 0 || "$#" != 3 ]]; then
  echo 'Usage: sudo bash install-release.sh ARCHIVE RELEASE_ID SHA256' >&2
  exit 2
fi
archive=$1
release_id=$2
expected_sha=$3
[[ "$release_id" =~ ^[a-zA-Z0-9._-]+$ && "$expected_sha" =~ ^[a-f0-9]{64}$ ]] || exit 2
actual_sha=$(sha256sum "$archive" | cut -d ' ' -f 1)
[[ "$actual_sha" == "$expected_sha" ]] || { echo 'Archive checksum mismatch' >&2; exit 1; }
node -e 'const [a,b]=process.versions.node.split(".").map(Number);if(a<22||(a===22&&b<14))process.exit(1)'
base=/opt/fba-fee-calculator
release_dir="$base/releases/$release_id"
[[ ! -e "$release_dir" ]] || { echo 'Release directory already exists; use a new release ID' >&2; exit 1; }
if ! id fba-calculator >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$base" --shell /usr/sbin/nologin fba-calculator
fi
install -d -m 0755 "$base/releases" "$base/backups" "$release_dir"
install -d -m 0750 -o root -g fba-calculator /etc/fba-fee-calculator
tar --no-same-owner -xzf "$archive" -C "$release_dir"
chown -R root:fba-calculator "$release_dir"
chmod -R u=rwX,g=rX,o= "$release_dir"
if [[ ! -f /etc/fba-fee-calculator/service.env ]]; then
  install -m 0640 -o root -g fba-calculator "$release_dir/deploy/linux/service.env.example" /etc/fba-fee-calculator/service.env
fi
cd "$release_dir"
runuser -u fba-calculator -- node scripts/check-rates.mjs
runuser -u fba-calculator -- node --test tests/*.test.mjs
previous=''
if [[ -L "$base/current" && -d "$base/current" ]]; then
  candidate=$(readlink -f "$base/current")
  if [[ "$candidate" == "$base/releases/"* && -f "$candidate/src/server.mjs" ]]; then
    previous=$candidate
    printf '%s\n' "$previous" > "$base/backups/previous-$release_id.txt"
  fi
fi
if [[ -f /etc/systemd/system/fba-fee-calculator.service ]]; then
  cp /etc/systemd/system/fba-fee-calculator.service "$base/backups/unit-$release_id.service"
fi
install -m 0644 "$release_dir/deploy/linux/fba-fee-calculator.service" /etc/systemd/system/fba-fee-calculator.service
ln -s "$release_dir" "$base/current-next"
mv -Tf "$base/current-next" "$base/current"
systemctl daemon-reload
systemctl enable fba-fee-calculator
systemctl restart fba-fee-calculator
for attempt in {1..15}; do
  if curl -fsS http://127.0.0.1:8791/readyz >/dev/null; then
    printf 'Release ready: %s\n' "$release_id"
    exit 0
  fi
  sleep 1
done
echo 'Readiness failed; restoring previous release when available' >&2
if [[ -n "$previous" && -d "$previous" ]]; then
  ln -s "$previous" "$base/current-rollback"
  mv -Tf "$base/current-rollback" "$base/current"
  if [[ -f "$base/backups/unit-$release_id.service" ]]; then
    cp "$base/backups/unit-$release_id.service" /etc/systemd/system/fba-fee-calculator.service
    systemctl daemon-reload
  fi
  systemctl restart fba-fee-calculator
else
  systemctl stop fba-fee-calculator
fi
exit 1
