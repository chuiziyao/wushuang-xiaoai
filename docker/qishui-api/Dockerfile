# qishui-api sidecar 构建文件（qishui-api，MIT）
# 由 scripts/fetch-upstream.* 复制为 reference/qishui-api/Dockerfile.wushuang
FROM node:lts-alpine

ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN apk add --no-cache tini

ENV NODE_ENV=production
WORKDIR /app
COPY . ./
RUN npm ci --omit=dev --registry=${NPM_REGISTRY} \
    && chown -R node:node /app
USER node

EXPOSE 3300
CMD [ "/sbin/tini", "--", "node", "app.js" ]
