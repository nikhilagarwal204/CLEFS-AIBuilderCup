# The web app (Next.js) for Google Cloud Run. next.config.mjs fixes the
# /api/clefs rewrite at build time, so the API origin is a build argument
# (client/cloudbuild.yaml passes it); NEXT_PUBLIC_API_URL is the same origin
# as the browser reaches it.
FROM node:22-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG CLEFS_API_ORIGIN
ARG NEXT_PUBLIC_API_URL
ENV CLEFS_API_ORIGIN=$CLEFS_API_ORIGIN \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build && npm prune --omit=dev

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app ./
USER node
EXPOSE 8080
CMD ["sh", "-c", "exec node_modules/.bin/next start -H 0.0.0.0 -p ${PORT:-8080}"]
