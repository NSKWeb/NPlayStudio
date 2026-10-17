#!/usr/bin/env bash
# One-shot VPS setup for NPlayStudio (Debian/Ubuntu).
#
#   sudo ./scripts/vps-bootstrap.sh <domain> [email]
#
# Idempotent: safe to re-run. It installs Docker + nginx + certbot, deploys the
# app to /srv/nplaystudio, starts the compose stack, puts nginx in front, and
# obtains a Let's Encrypt certificate. The [email] is passed to certbot for
# expiry notices; when omitted certbot runs without one.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ]; then
    echo "usage: sudo $0 <domain> [certbot-email]" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "error: run as root (sudo $0 $DOMAIN)" >&2
    exit 2
fi

APP_DIR=/srv/nplaystudio
SITE=nplaystudio

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg nginx certbot python3-certbot-nginx git

say "Installing Docker"
if ! command -v docker >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$(. /etc/os-release; echo "$ID")/gpg \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/$(. /etc/os-release; echo "$ID") \
$(. /etc/os-release; echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

say "Deploying the application to $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --all --prune
    git -C "$APP_DIR" reset --hard origin/main
else
    mkdir -p "$APP_DIR"
    git clone https://github.com/NSKWeb/NPlayStudio.git "$APP_DIR"
fi

if [ ! -f "$APP_DIR/backend/.env" ]; then
    cp "$APP_DIR/backend/.env.example" "$APP_DIR/backend/.env"
    echo "created backend/.env from the example — review it"
fi

say "Building and starting the stack"
cd "$APP_DIR"
docker compose build
docker compose up -d --remove-orphans

say "Waiting for the API healthcheck"
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
        echo "API is up"
        break
    fi
    sleep 2
done
curl -fsS http://127.0.0.1:8000/health >/dev/null || {
    echo "error: API did not become healthy; check 'docker compose logs api'" >&2
    exit 1
}

say "Configuring nginx for $DOMAIN"
mkdir -p /var/www/certbot
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    # First run: HTTP-only config so certbot can serve the ACME challenge.
    sed "s/nplaystudio\.example\.com/$DOMAIN/g" \
        "$APP_DIR/nginx/nplaystudio.pre-certbot.conf" \
        > "/etc/nginx/sites-available/$SITE"
    ln -sf "/etc/nginx/sites-available/$SITE" "/etc/nginx/sites-enabled/$SITE"
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl reload nginx

    say "Obtaining the certificate"
    if [ -n "$EMAIL" ]; then
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
            --redirect -m "$EMAIL"
    else
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
            --redirect --register-unsafely-without-email
    fi
fi

say "Installing the hardened site config"
sed "s/nplaystudio\.example\.com/$DOMAIN/g" \
    "$APP_DIR/nginx/nginx.conf" \
    > "/etc/nginx/sites-available/$SITE"
nginx -t
systemctl reload nginx

say "Verifying HTTPS"
curl -fsS "https://$DOMAIN/health" && echo

say "Done"
echo "Site:      https://$DOMAIN"
echo "API docs:  https://$DOMAIN/docs"
echo "Renewals:  systemctl status certbot.timer"
