# Use official Node.js image
FROM node:18

# Create app directory
WORKDIR /app

# Copy package files first, then install dependencies
COPY package*.json ./
RUN npm install

# Copy rest of the app
COPY . .

# Start the app
CMD ["npm", "start"]

# Expose port
EXPOSE 3000
