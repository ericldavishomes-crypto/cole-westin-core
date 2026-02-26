FROM nvidia/cuda:12.2.0-base-ubuntu22.04

# 1. Install Node.js the most stable way possible
RUN apt-get update && apt-get install -y nodejs npm

# 2. Setup your app
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .

# 3. Start the server
CMD ["node", "index.js"]
