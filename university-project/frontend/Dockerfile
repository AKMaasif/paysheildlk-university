FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

ENV API_PROXY_URL=http://backend:8000
RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
