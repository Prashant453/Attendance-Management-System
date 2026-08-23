# Production Dockerfile for Attendify Vision Backend
FROM python:3.10-slim

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies for OpenCV, dlib and TiDB SSL connections
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libgl1 \
    libglib2.0-0 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY . .

# Expose default port
EXPOSE 5000

# Start Gunicorn server binding to PORT environment variable (Render sets $PORT dynamically)
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --workers 2 --timeout 120"]

