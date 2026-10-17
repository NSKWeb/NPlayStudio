#!/usr/bin/env bash
# Replace the nplaystudio.example.com placeholders with the real domain.
#
#   ./scripts/configure-domain.sh nplaystudio.com [contact-email]
#
# The email argument is optional; when omitted the domain is used for all three
# addresses (support@, dmca@, privacy@). Run it from anywhere.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ]; then
    echo "usage: $0 <domain> [contact-email]" >&2
    echo "  e.g. $0 nplaystudio.com" >&2
    exit 2
fi

# Strip a scheme or trailing slash if one was pasted in by mistake.
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%/}"

case "$DOMAIN" in
    *[!a-zA-Z0-9.-]*|.*|*.)
        echo "error: '$DOMAIN' does not look like a hostname" >&2
        exit 2
        ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Replacing nplaystudio.example.com -> $DOMAIN"

# Files that embed the hostname and/or the contact addresses.
FILES=(
    README.md
    nginx/nginx.conf
    frontend/robots.txt
    frontend/sitemap.xml
    frontend/contact.html
    frontend/cookie.html
    frontend/disclaimer.html
    frontend/dmca.html
    frontend/gdpr.html
    frontend/privacy.html
    frontend/terms.html
)

if [ -n "$EMAIL" ]; then
    for file in "${FILES[@]}"; do
        [ -f "$file" ] || continue
        sed -i "s/[a-z]\+@nplaystudio\.example\.com/$EMAIL/g" "$file"
    done
    echo "Contact addresses set to $EMAIL"
else
    for role in support dmca privacy; do
        for file in "${FILES[@]}"; do
            [ -f "$file" ] || continue
            sed -i "s/${role}@nplaystudio\.example\.com/${role}@${DOMAIN}/g" "$file"
        done
    done
    echo "Contact addresses set to support@$DOMAIN, dmca@$DOMAIN, privacy@$DOMAIN"
fi

# Any remaining hostname mentions (nginx server_name, sitemap, robots, prose).
for file in "${FILES[@]}"; do
    [ -f "$file" ] || continue
    sed -i "s/nplaystudio\.example\.com/$DOMAIN/g" "$file"
done

echo
echo "Remaining placeholder references:"
if grep -rn "nplaystudio\.example\.com" . \
        --exclude-dir=.git --exclude-dir=__pycache__ 2>/dev/null; then
    echo
    echo "note: update the AdSense publisher id in frontend/assets/app.js separately."
else
    echo "  none"
fi

echo
echo "Next: reload nginx and request the certificate."
echo "  sudo nginx -t && sudo systemctl reload nginx"
echo "  sudo certbot --nginx -d $DOMAIN"
