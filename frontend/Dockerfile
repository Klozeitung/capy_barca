# Pinned to the Node 22 LTS line. The builder stage produces static assets and
# is discarded, so it never reaches the running system.
FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run test
RUN npm run build

# nginx-unprivileged instead of the stock nginx image: the stock image starts
# its master process as root and keeps its pid file, cache directories and
# template output under paths only root may write. This variant is built for
# non-root operation and still supports /etc/nginx/templates.
#
# Pinned to the 1.29 line. The previous 'nginx:alpine' floated across major
# versions, which is the gap this closes; staying on the minor line keeps
# CVE rebuilds arriving without a repository edit.
FROM nginxinc/nginx-unprivileged:1.29-alpine

# Must match the backend so both containers can read the same ssl/*.pem, whose
# private key is readable by owner and group only.
ARG APP_UID=1000
ARG APP_GID=1000

# The base image prepares its writable paths for its own default account, so
# they have to be handed over to the account this image actually runs as.
# The pid file lives under /tmp in this image and needs no adjustment.
USER root
RUN chown -R "${APP_UID}:${APP_GID}" \
        /var/cache/nginx \
        /etc/nginx/conf.d \
        /usr/share/nginx/html

COPY --from=builder --chown=${APP_UID}:${APP_GID} /app/dist /usr/share/nginx/html
COPY --chown=${APP_UID}:${APP_GID} nginx.conf /etc/nginx/templates/default.conf.template

USER ${APP_UID}:${APP_GID}

# No EXPOSE: the listening port comes from PORT_FRONTEND, substituted into the
# template at container start, so any fixed value here would be misleading.
# docker-compose.yml publishes the port.
