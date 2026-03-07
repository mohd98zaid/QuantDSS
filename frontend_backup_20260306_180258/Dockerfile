FROM node:20-alpine AS base

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

# Build
COPY . .
RUN npm run build

# Production image with standalone output
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production

# Copy standalone build files
COPY --from=base /app/.next/standalone ./
COPY --from=base /app/.next/static ./.next/static

# Copy public assets (may be empty)
COPY --from=base /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
