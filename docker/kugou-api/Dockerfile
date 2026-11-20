# kugou-api sidecar 构建文件（KuGouMusicApi，MIT）
# 由 scripts/fetch-upstream.* 复制为 reference/KuGouMusicApi/Dockerfile.wushuang
FROM node:lts-alpine

ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN apk add --no-cache tini \
    && npm install -g pnpm@9 --registry=${NPM_REGISTRY}

ENV NODE_ENV=production
WORKDIR /app
COPY . ./
RUN pnpm install --frozen-lockfile --prod --ignore-scripts --registry=${NPM_REGISTRY} \
    && chown -R node:node /app
USER node

EXPOSE 3000
CMD [ "/sbin/tini", "--", "node", "app.js" ]
