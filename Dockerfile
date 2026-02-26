FROM nvidia/cuda:12.2.0-base-ubuntu22.04

WORKDIR /app

COPY package*.json ./

RUN npm install

COPY . .

CMD ["node", "index.js"]
