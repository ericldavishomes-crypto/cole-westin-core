FROM nvidia/cuda:12.2.0-base-ubuntu22.04

# 1. Clear the decks and install Node.js the "Old Reliable" way
RUN apt-get update && \
    apt-get install -y curl ca-certificates && \
    curl -sL https://deb.nodesource.com | bash - && \
    apt-get install -y nodejs

# 2. Verify it worked (this will show in your logs)
RUN node -v && npm -v

# 3. Setup your app
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .

# 4. Start the server
CMD ["node", "index.js"]
