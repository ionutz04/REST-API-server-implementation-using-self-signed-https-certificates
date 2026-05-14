#!/bin/bash

# Build the image (use --no-cache if needed)
docker build -t forecasting .

# Run with Jetson GPU support
docker run --rm -it \
    --runtime nvidia \
    --gpus all \
    --privileged \
    --network host \
    -e REDIS_HOST=localhost \
    -e REDIS_PORT=6379 \
    -e NVIDIA_VISIBLE_DEVICES=all \
    -e NVIDIA_DRIVER_CAPABILITIES=all \
    -e LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/tegra:/usr/lib/aarch64-linux-gnu/nvidia:/usr/local/cuda/lib64:/usr/lib/aarch64-linux-gnu \
    -v /usr/lib/aarch64-linux-gnu/tegra:/usr/lib/aarch64-linux-gnu/tegra:ro \
    -v /usr/lib/aarch64-linux-gnu/tegra-egl:/usr/lib/aarch64-linux-gnu/tegra-egl:ro \
    -v /usr/lib/aarch64-linux-gnu/nvidia:/usr/lib/aarch64-linux-gnu/nvidia:ro \
    -v /usr/local/cuda:/usr/local/cuda:ro \
    -v /etc/nv_tegra_release:/etc/nv_tegra_release:ro \
    -v ./:/app \
    forecasting
