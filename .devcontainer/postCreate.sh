#!/usr/bin/env bash
set -euo pipefail

# 作業ディレクトリをワークスペースに揃える
cd "${containerWorkspaceFolder:-/workspaces/ShogiArena}" || exit 1

echo "[postCreate] Running apt-get update..."
sudo apt-get update

echo "[postCreate] Installing build dependencies..."
sudo apt-get install -y \
  clang \
  default-libmysqlclient-dev \
  build-essential \
  pkg-config \
  lsb-release \
  wget \
  software-properties-common \
  gnupg \
  ripgrep \
  socat

echo "[postCreate] Setting up NVIDIA CUDA apt repository..."
if ! dpkg -s cuda-keyring >/dev/null 2>&1; then
  CUDA_REPO_URL="https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64"
  wget -q "${CUDA_REPO_URL}/cuda-keyring_1.1-1_all.deb"
  sudo dpkg -i cuda-keyring_1.1-1_all.deb
  rm -f cuda-keyring_1.1-1_all.deb
fi

echo "[postCreate] Installing TensorRT libraries..."
sudo apt-get update
TENSORRT_VERSION="${TENSORRT_VERSION:-8.6.1.6}"
TENSORRT_APT_VERSION="$(apt-cache madison libnvinfer8 | awk -v v="${TENSORRT_VERSION}" '$3 ~ ("^" v) { print $3; exit }')"
if [ -z "${TENSORRT_APT_VERSION}" ]; then
  echo "[postCreate] TensorRT ${TENSORRT_VERSION} not found in apt repo. Falling back to latest available."
  sudo apt-get install -y --no-install-recommends \
    libnvinfer8 \
    libnvinfer-dev \
    libnvinfer-headers-dev \
    libnvinfer-plugin8 \
    libnvinfer-plugin-dev \
    libnvinfer-headers-plugin-dev \
    libnvonnxparsers8 \
    libnvonnxparsers-dev \
    libnvparsers8 \
    libnvparsers-dev
else
  sudo apt-get install -y --no-install-recommends \
    "libnvinfer8=${TENSORRT_APT_VERSION}" \
    "libnvinfer-dev=${TENSORRT_APT_VERSION}" \
    "libnvinfer-headers-dev=${TENSORRT_APT_VERSION}" \
    "libnvinfer-plugin8=${TENSORRT_APT_VERSION}" \
    "libnvinfer-plugin-dev=${TENSORRT_APT_VERSION}" \
    "libnvinfer-headers-plugin-dev=${TENSORRT_APT_VERSION}" \
    "libnvonnxparsers8=${TENSORRT_APT_VERSION}" \
    "libnvonnxparsers-dev=${TENSORRT_APT_VERSION}" \
    "libnvparsers8=${TENSORRT_APT_VERSION}" \
    "libnvparsers-dev=${TENSORRT_APT_VERSION}"
fi

wget https://apt.llvm.org/llvm.sh
chmod +x llvm.sh
sudo ./llvm.sh 18 all
rm llvm.sh
sudo update-alternatives --install /usr/bin/clang clang /usr/bin/clang-18 100
sudo update-alternatives --install /usr/bin/clang++ clang++ /usr/bin/clang++-18 100

echo "[postCreate] Installing Node CLI tools (claude-code, gemini-cli)..."
npm install -g @anthropic-ai/claude-code @google/gemini-cli @openai/codex

echo "[postCreate] Syncing Python environment with uv..."
uv sync --all-extras

echo "[postCreate] Done."
