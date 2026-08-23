# Production Fast-Build Dockerfile for Attendify Vision Backend
FROM python:3.10-slim

# Prevent interactive prompts, buffer output, and constrain CMake RAM
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend:/app \
    CMAKE_BUILD_PARALLEL_LEVEL=1 \
    CFLAGS="-O1"

# Install pre-compiled C++ dlib, OpenCV and system libraries (Fast Debian binaries - no long compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libgl1 \
    libglib2.0-0 \
    ca-certificates \
    python3-dlib \
    python3-numpy \
    && cp -r /usr/lib/python3/dist-packages/dlib* /usr/local/lib/python3.10/site-packages/ 2>/dev/null || true \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY . .

# Expose default port
EXPOSE 5000

# Start Gunicorn server binding to PORT environment variable
CMD ["sh", "-c", "if [ -d 'backend' ]; then gunicorn app:app --chdir backend --bind 0.0.0.0:${PORT:-5000} --workers 2 --timeout 120; else gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --workers 2 --timeout 120; fi"]



