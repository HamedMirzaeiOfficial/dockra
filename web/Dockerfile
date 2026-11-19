FROM node:22-alpine AS build
ARG NPM_REGISTRY=
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY package.json package-lock.json ./
# On restricted networks set NPM_REGISTRY to an internal npm mirror. Versions come from package-lock.json.
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
    && npm ci --no-audit --no-fund
COPY . .
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# Extra origins for the CSP connect-src (space-separated), e.g. a websocket service on another host.
ARG NEXT_PUBLIC_EXTRA_CONNECT_SRC=
ENV NEXT_PUBLIC_EXTRA_CONNECT_SRC=$NEXT_PUBLIC_EXTRA_CONNECT_SRC
RUN npm run build
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
