# Serving the bare prefix directly and disabling absolute_redirect are both
# load-bearing: Fred's iframe URL has no trailing slash and forwards redirects.

# The palette and the frame's side of the host protocol are installed from
# Fred's published packages rather than kept as copies here. The typeface comes
# with the palette, so the frame renders in Fred's own face without a binary
# living in the source tree.
FROM node:22.13.0-alpine AS packages
WORKDIR /packages
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM nginx:1.27-alpine

RUN mkdir -p /usr/share/nginx/html/apps/review-board
COPY index.html styles.css dashboard.mjs view-model.mjs frame-bridge.mjs /usr/share/nginx/html/apps/review-board/
COPY --from=packages /packages/node_modules/@fred-oss/iframe-sdk/dist/index.js \
     /usr/share/nginx/html/apps/review-board/fred-iframe-sdk.js
COPY --from=packages /packages/node_modules/@fred-oss/design-tokens/dist/tokens.css \
     /usr/share/nginx/html/apps/review-board/fred-tokens.css
COPY --from=packages /packages/node_modules/@fred-oss/design-tokens/dist/fonts.css \
     /usr/share/nginx/html/apps/review-board/fred-fonts.css
COPY --from=packages /packages/node_modules/@fred-oss/design-tokens/dist/fonts/ \
     /usr/share/nginx/html/apps/review-board/fonts/

RUN printf '%s\n' \
  'server {' \
  '  listen 80;' \
  '  root /usr/share/nginx/html;' \
  '  absolute_redirect off;' \
  '  location = /apps/review-board { add_header Cache-Control "no-store"; try_files /apps/review-board/index.html =404; }' \
  '  location ~ ^/apps/review-board/[^/]+\.mjs$ { default_type application/javascript; add_header Cache-Control "no-store"; try_files $uri =404; }' \
  '  location ~ ^/apps/review-board/(fred-[^/]+|fonts/[^/]+)$ { try_files $uri =404; }' \
  '  location /apps/review-board/ {' \
  '    add_header Cache-Control "no-store";' \
  '    try_files $uri $uri/ /apps/review-board/index.html;' \
  '  }' \
  '  location = /healthz { return 200 "ok"; add_header content-type text/plain; }' \
  '}' > /etc/nginx/conf.d/default.conf
