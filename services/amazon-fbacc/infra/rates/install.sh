#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" != 0 || "$#" != 4 ]]; then
  echo 'Usage: sudo bash install.sh ARCHIVE RELEASE_ID SHA256 WHEEL_DIRECTORY' >&2
  exit 2
fi
monitor_archive=$1
monitor_release=$2
monitor_sha=$3
monitor_wheels=$4
[[ "$monitor_release" =~ ^[a-zA-Z0-9._-]+$ && "$monitor_sha" =~ ^[a-f0-9]{64}$ ]]
[[ "$(sha256sum "$monitor_archive" | cut -d ' ' -f 1)" == "$monitor_sha" ]]
id fba-calculator >/dev/null
if ! id fba-rate-monitor >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/fba-rate-monitor --shell /usr/sbin/nologin --groups fba-calculator fba-rate-monitor
fi
monitor_dir=/opt/fba-rate-monitor/releases/$monitor_release
[[ ! -e "$monitor_dir" ]]
install -d -m 0755 /opt/fba-rate-monitor/releases
install -d -m 0750 -o root -g fba-rate-monitor "$monitor_dir"
tar --no-same-owner -xzf "$monitor_archive" -C "$monitor_dir"
chown -R root:fba-rate-monitor "$monitor_dir"
chmod -R u=rwX,g=rX,o= "$monitor_dir"
if [[ ! -x /opt/fba-rate-monitor/venv/bin/python ]]; then
  python3 -m venv /opt/fba-rate-monitor/venv
fi
/opt/fba-rate-monitor/venv/bin/pip install --no-index --require-hashes --find-links "$monitor_wheels" -r "$monitor_dir/requirements.txt"
install -d -m 0750 -o fba-rate-monitor -g fba-rate-monitor /var/lib/fba-rate-monitor
install -d -m 0700 -o root -g root /etc/fba-rate-monitor/approvals
runuser -u fba-rate-monitor -- env PYTHONDONTWRITEBYTECODE=1 /opt/fba-rate-monitor/venv/bin/python -m unittest discover -s "$monitor_dir/tests" -v
ln -s "$monitor_dir" /opt/fba-rate-monitor/current-next
mv -Tf /opt/fba-rate-monitor/current-next /opt/fba-rate-monitor/current
install -m 0644 "$monitor_dir/fba-rate-monitor.service" /etc/systemd/system/fba-rate-monitor.service
install -m 0644 "$monitor_dir/fba-rate-monitor.timer" /etc/systemd/system/fba-rate-monitor.timer
systemd-analyze verify /etc/systemd/system/fba-rate-monitor.service /etc/systemd/system/fba-rate-monitor.timer
systemctl daemon-reload
printf 'Installed monitor %s; start a check, inspect results, then enable the timer.\n' "$monitor_release"
