FROM node:22-bookworm-slim

WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./

ARG NEXT_PUBLIC_API_URL=
ARG API_PROXY_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    API_PROXY_URL=$API_PROXY_URL \
    HOSTNAME=0.0.0.0

RUN npm run build
EXPOSE 3000
CMD ["npm", "start", "--", "-H", "0.0.0.0"]
