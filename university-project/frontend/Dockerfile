FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

ENV API_PROXY_URL=http://backend:8000
# The browser reads these two when the site is built, not when it runs, so they are build arguments.
ARG NEXT_PUBLIC_WS_URL=
ARG NEXT_PUBLIC_WS_PORT=8000
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL NEXT_PUBLIC_WS_PORT=$NEXT_PUBLIC_WS_PORT
RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
