# 网页版大模型中转通用构建文件（doubao-free-api-1 / Qwen-Free-API 共用）
# 修复：新版 node:lts-alpine 不再自带 yarn；统一走国内 registry。
# 由 scripts/fetch-upstream.* 复制为各仓库的 Dockerfile.wushuang
FROM node:lts-alpine AS build
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm install -g yarn --registry=${NPM_REGISTRY}
WORKDIR /app
COPY . .
RUN yarn install --registry ${NPM_REGISTRY} --ignore-engines && yarn run build

FROM node:lts-alpine
WORKDIR /app
COPY --from=build /app/configs ./configs
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/dist ./dist
COPY --from=build /app/public ./public
COPY --from=build /app/node_modules ./node_modules
# DSM7 套件 worker 会以 package 用户 UID 运行容器（任意 UID），
# 应用需写 /app/logs 等目录，故放开写权限
RUN mkdir -p /app/logs && chmod -R a+w /app
EXPOSE 8000
CMD ["npm", "start"]
