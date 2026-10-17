#!/usr/bin/env bash
# Post-deploy smoke test: exercise the public HTTPS endpoint end to end.
#
#   ./scripts/smoke-test.sh nplaystudio.com
#
# Verifies DNS, the certificate, the HTTP->HTTPS redirect, the static pages,
# the API docs proxy, and a real media job (upload -> queue -> worker ->
# download) through nginx. Run it on the VPS or anywhere with network access.
set -uo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
    echo "usage: $0 <domain> [--local]" >&2
    exit 2
fi

# --local skips DNS/cert checks and talks to https://127.0.0.1 with -k, which
# is what you want when testing the nginx layer before DNS is live.
LOCAL=0
[ "${2:-}" = "--local" ] && LOCAL=1

if [ "$LOCAL" = 1 ]; then
    BASE="https://127.0.0.1"
    CURL=(curl -sk --resolve "$DOMAIN:443:127.0.0.1")
else
    BASE="https://$DOMAIN"
    CURL=(curl -fsS)
fi

pass=0
fail=0

ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }
step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "DNS"
if [ "$LOCAL" = 1 ]; then
    echo "  skipped (--local)"
else
    resolved="$(dig +short A "$DOMAIN" | tail -1)"
    if [ -n "$resolved" ]; then ok "$DOMAIN -> $resolved"; else bad "$DOMAIN does not resolve"; fi
fi

step "TLS certificate"
if [ "$LOCAL" = 1 ]; then
    echo "  skipped (--local)"
else
    if echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null \
            | openssl x509 -noout -checkend 86400 >/dev/null 2>&1; then
        ok "certificate valid for at least 24h"
    else
        bad "certificate missing, invalid, or expiring within 24h"
    fi
fi

step "HTTP to HTTPS redirect"
if [ "$LOCAL" = 1 ]; then
    target="http://127.0.0.1/"
    host=(-H "Host: $DOMAIN")
else
    target="http://$DOMAIN/"
    host=()
fi
code="$(curl -s -o /dev/null -w '%{http_code}' "${host[@]}" "$target" 2>/dev/null)"
if [ "$code" = "301" ] || [ "$code" = "302" ]; then
    ok "$target redirects ($code)"
else
    bad "$target returned $code (expected a 301/302)"
fi

step "API health"
body="$("${CURL[@]}" "$BASE/health" 2>/dev/null)"
if echo "$body" | grep -q '"status"'; then ok "/health -> $body"; else bad "/health -> $body"; fi

step "Static pages"
for path in / /video-compress.html /audio-extract.html /image-crop.html \
            /privacy.html /terms.html /contact.html /robots.txt /sitemap.xml; do
    code="$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE$path" 2>/dev/null)"
    if [ "$code" = "200" ]; then ok "$path"; else bad "$path returned $code"; fi
done

step "API docs proxy"
for path in /docs /openapi.json; do
    code="$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE$path" 2>/dev/null)"
    if [ "$code" = "200" ]; then ok "$path"; else bad "$path returned $code"; fi
done

step "Security headers"
headers="$("${CURL[@]}" -D - -o /dev/null "$BASE/" 2>/dev/null)"
for header in "X-Content-Type-Options" "X-Frame-Options" "Referrer-Policy"; do
    if echo "$headers" | grep -qi "^$header:"; then ok "$header present"; else bad "$header missing"; fi
done

step "Media job end to end"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -hide_banner -loglevel error -y \
        -f lavfi -i testsrc=size=320x240:rate=25:duration=2 \
        -f lavfi -i sine=frequency=440:duration=2 \
        -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$tmp/in.mp4" 2>/dev/null
else
    echo "  ffmpeg not found; generating a tiny sample is not possible"
fi

if [ -s "$tmp/in.mp4" ]; then
    resp="$("${CURL[@]}" -F "file=@$tmp/in.mp4;type=video/mp4" \
            -F "resolution=320x240" -F "quality=small" -F "format=mp4" -F "codec=h264" \
            "$BASE/api/video/compress" 2>/dev/null)"
    task_id="$(echo "$resp" | sed -n 's/.*"task_id":"\([^"]*\)".*/\1/p')"
    if [ -n "$task_id" ]; then
        ok "upload queued (task ${task_id:0:8})"
        status=""
        for _ in $(seq 1 60); do
            status="$("${CURL[@]}" "$BASE/api/status/$task_id" 2>/dev/null \
                      | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')"
            case "$status" in done|failed) break;; esac
            sleep 2
        done
        if [ "$status" = "done" ]; then
            size="$("${CURL[@]}" -o "$tmp/out.mp4" -w '%{size_download}' "$BASE/api/file/$task_id" 2>/dev/null)"
            # The ISO-BMFF 'ftyp' box starts at offset 4, after the box size.
            if [ "${size:-0}" -gt 1000 ] && head -c 12 "$tmp/out.mp4" | grep -qa "ftyp"; then
                ok "job finished and download is a valid MP4 ($size bytes)"
            else
                bad "download looks wrong (size=$size)"
            fi
        else
            bad "job status is '$status'"
        fi
    else
        bad "upload rejected: ${resp:0:200}"
    fi
else
    echo "  skipped (no sample media)"
fi

step "Result"
printf '  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
