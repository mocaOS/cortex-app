FROM node:20-alpine

WORKDIR /app

# Install curl for downloading custom logo
RUN apk add --no-cache curl

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm install

# Copy source code
COPY . .

# Download custom logo if NEXT_PUBLIC_LOGO_URL is set
# Extracts the file extension and saves with correct format
RUN if [ -n "$NEXT_PUBLIC_LOGO_URL" ]; then \
        echo "Downloading custom logo from $NEXT_PUBLIC_LOGO_URL"; \
        EXT="${NEXT_PUBLIC_LOGO_URL##*.}"; \
        # Remove query parameters from extension if present
        EXT=$(echo "$EXT" | cut -d'?' -f1); \
        curl -fsSL "$NEXT_PUBLIC_LOGO_URL" -o "/app/public/custom-logo.$EXT" && \
        echo "Custom logo downloaded successfully as custom-logo.$EXT"; \
    else \
        echo "No custom logo URL provided, using default logo"; \
    fi

# Expose port
EXPOSE 3000

# Run in development mode
CMD ["npm", "run", "dev"]
