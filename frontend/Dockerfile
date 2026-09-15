# Build context is this directory. Produces a proxy image with the built SPA
# inside it, so there is nothing to upload separately and no bundle on the host
# to go stale against the code.
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Same origin as the UI, so the browser never makes a cross-origin request and
# the certificate covers the API too. The proxy strips the prefix again.
ENV VITE_API_URL=/api
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /src/dist /srv
