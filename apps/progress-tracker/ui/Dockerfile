# Serving the bare prefix directly and disabling absolute_redirect are both
# load-bearing, not style: the iframe src has no trailing slash, and Fred
# forwards Location verbatim. Full rationale: README.md. The installed assets
# are excluded from the page fallback so a missing one answers 404 rather than
# returning the page under its name, which fails silently and unstyled.

# The frame's palette and its side of the Fred protocol are installed from
# Fred's published packages rather than copied in. Nothing is bundled: both
# artifacts ship as a plain stylesheet and a self-contained ES module.
FROM node:22.13.0-alpine AS packages
WORKDIR /packages
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM nginx:1.27-alpine

RUN mkdir -p /usr/share/nginx/html/apps/progress-tracker
COPY index.html /usr/share/nginx/html/apps/progress-tracker/index.html
COPY --from=packages /packages/node_modules/@fred-oss/design-tokens/dist/tokens.css \
     /usr/share/nginx/html/apps/progress-tracker/fred-tokens.css
COPY --from=packages /packages/node_modules/@fred-oss/iframe-sdk/dist/index.js \
     /usr/share/nginx/html/apps/progress-tracker/fred-iframe-sdk.js

RUN printf '%s\n' \
  'server {' \
  '  listen 80;' \
  '  root /usr/share/nginx/html;' \
  '  absolute_redirect off;' \
  '  location = /apps/progress-tracker { add_header Cache-Control "no-store"; try_files /apps/progress-tracker/index.html =404; }' \
  '  location ~ ^/apps/progress-tracker/fred-[^/]+$ { try_files $uri =404; }' \
  '  location /apps/progress-tracker/ {' \
  '    add_header Cache-Control "no-store";' \
  '    try_files $uri $uri/ /apps/progress-tracker/index.html;' \
  '  }' \
  '  location = /healthz { return 200 "ok"; add_header content-type text/plain; }' \
  '}' > /etc/nginx/conf.d/default.conf
