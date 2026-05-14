# For JetPack 6.x - use dustynv image (no auth required)
# Or use nvcr.io/nvidia/l4t-tensorflow:r36.3.0-tf2.16-py3 (requires NGC login)
FROM dustynv/l4t-tensorflow:tf2-r36.2.0

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt redis

# Copy the application code
COPY run_forecast.py .

# Environment variables for Redis connection
ENV REDIS_HOST=localhost
ENV REDIS_PORT=6379

# Suppress TensorRT warnings if not using TensorRT
ENV TF_CPP_MIN_LOG_LEVEL=2

# Jetson GPU library paths
ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/tegra:/usr/lib/aarch64-linux-gnu/nvidia:/usr/local/cuda/lib64:/usr/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH}
ENV PATH=/usr/local/cuda/bin:${PATH}

CMD ["python3", "run_forecast.py"]
