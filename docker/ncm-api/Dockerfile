# ncm-api sidecar 构建文件（基于上游 Dockerfile，增加国内 registry 支持）
# 由 scripts/fetch-upstream.* 复制为 reference/ncm-api-enhanced/Dockerfile.wushuang
FROM node:lts-alpine

ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN apk add --no-cache tini \
    && npm install -g pnpm@9 --registry=${NPM_REGISTRY}

ENV NODE_ENV=production
WORKDIR /app
COPY . ./
# root 安装依赖（避免 WORKDIR 属主问题），完成后整体降权给 node 用户运行
RUN pnpm install --frozen-lockfile --prod --ignore-scripts --registry=${NPM_REGISTRY} \
    && chown -R node:node /app
USER node

EXPOSE 3000
CMD [ "/sbin/tini", "--", "node", "app.js" ]
