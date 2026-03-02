FROM node:20-alpine

WORKDIR /app

# Install deps first (better layer caching)
COPY package*.json ./
RUN npm ci

# Copy source
COPY . .

EXPOSE 3000

# Dev mode (tsx watch)
CMD ["npm", "run", "dev"]