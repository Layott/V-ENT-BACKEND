#!/usr/bin/env bash
# Organisers' own addresses on the box (P8c). Run by vent-custom-domains.timer
# every five minutes, as root.
#
# For every name V-ENT lists as active (manage.py custom_domains: a CNAME to
# sites.v-ent.co checked, on a Premium record), get a certificate once with
# certbot's webroot (the challenge is answered by the port 80 block in
# nginx-custom-domains.conf) and write one 443 block that hands the name to the
# site, which maps it to its record (src/lib/customHost.js). A name that is no
# longer active loses its block. nginx is reloaded only when a block changed
# and only after `nginx -t` passes. Certificates renew with certbot's own timer.
#
# Remove: systemctl disable --now vent-custom-domains.timer; rm the two units,
# /etc/nginx/vent-custom/ and /etc/nginx/sites-enabled/vent-custom; reload nginx.
set -u
BACKEND=/srv/vent/backend
WEBROOT=/var/www/certbot
BLOCKS=/etc/nginx/vent-custom
mkdir -p "$WEBROOT" "$BLOCKS"
HOSTNAME_RE='^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'

names=$(cd "$BACKEND" && sudo -u vent "$BACKEND/venv/bin/python" manage.py custom_domains 2>/dev/null) || {
  echo "custom-domains: could not read the list; nothing changed"; exit 0; }

changed=0
keep=" "
for name in $names; do
  # The list is checked on the server already; checked again here, because
  # this name becomes a file name and a line of nginx configuration.
  [[ "$name" =~ $HOSTNAME_RE ]] || { echo "custom-domains: skipped $name"; continue; }
  keep="$keep$name "
  if [ ! -f "/etc/letsencrypt/live/$name/fullchain.pem" ]; then
    # A second gate before asking for a certificate: the API's own answer for
    # this name, so a list read some minutes ago cannot outlive a removal.
    code=$(curl -s -o /dev/null -w '%{http_code}' --unix-socket /run/vent/api.sock       -H 'Host: api.v-ent.co' -H 'X-Forwarded-Proto: https' "http://localhost/sites/tls-ask/?domain=$name")
    [ "$code" = 200 ] || { echo "custom-domains: tls-ask said $code for $name"; continue; }
    certbot certonly --webroot -w "$WEBROOT" -d "$name" --non-interactive --agree-tos \
      --keep-until-expiring --quiet || { echo "custom-domains: no certificate for $name yet"; continue; }
  fi
  block="$BLOCKS/$name.conf"
  tmp=$(mktemp)
  cat > "$tmp" <<NGINX
# Written by deploy/custom-domains.sh (P8c). Do not edit by hand.
server {
    listen 443 ssl http2;
    server_name $name;
    ssl_certificate /etc/letsencrypt/live/$name/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$name/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    limit_conn conn 40;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location /_next/static/ {
        alias /srv/vent/frontend/.next/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    location / {
        proxy_pass http://vent_web;
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_next_upstream_tries 2;
        proxy_next_upstream_timeout 10s;
        proxy_set_header Host              \$host;
        proxy_set_header X-Forwarded-Host  \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX
  if ! cmp -s "$tmp" "$block"; then mv "$tmp" "$block"; changed=1; else rm -f "$tmp"; fi
done

for block in "$BLOCKS"/*.conf; do
  [ -e "$block" ] || continue
  name=$(basename "$block" .conf)
  case "$keep" in *" $name "*) ;; *) rm -f "$block"; changed=1; echo "custom-domains: removed $name";; esac
done

if [ "$changed" = 1 ]; then
  if nginx -t -q; then systemctl reload nginx; echo "custom-domains: nginx reloaded"
  else echo "custom-domains: nginx -t failed, not reloaded"; exit 1; fi
fi
