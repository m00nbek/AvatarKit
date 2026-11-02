#!/bin/bash
# Installs Hunyuan3D 2.1 on the clothes pod (run by pod.py). The install and the model
# weights live on the network volume (/workspace), so a later pod only reinstalls the
# system packages, which takes about a minute.
#
# Fixes Hunyuan needs on this image (found in the 2026-10-28 bake-off):
# - its requirements point at mirrors in China that time out from here; dropped
# - bpy==4.0 is gone from PyPI; the OBJ to GLB step uses trimesh instead (make_item.py)
# - deepspeed is for training only; dropped
# - setuptools 81+ has no pkg_resources, which pytorch_lightning imports; pinned below 81
# - basicsr imports torchvision.transforms.functional_tensor, removed in newer torchvision
# - the inpaint extension's build script calls python3-config, which isn't on PATH
set -euo pipefail

W=/workspace
REPO=$W/Hunyuan3D-2.1
COMMIT=82920d643c0dc2f7bfd7255f45f62d386edfe60c
export PATH=/usr/local/cuda/bin:$PATH CUDA_HOME=/usr/local/cuda
export TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0" MAX_JOBS=16
export UV_HTTP_TIMEOUT=600 UV_LOCK_TIMEOUT=3600
export HF_HOME=$W/hf HY3DGEN_MODELS=$W/hy3dgen

echo "== system packages"
apt-get update -qq > /dev/null
apt-get install -y -qq python3.10-dev libgl1 libglib2.0-0 libsm6 libxrender1 libxext6 > /dev/null
pip install -q uv

if [ -f $W/.installed ]; then
  echo "== Hunyuan already installed on the volume"
  exit 0
fi

echo "== Hunyuan3D 2.1 at $COMMIT"
[ -d $REPO ] || git clone -q https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git $REPO
git -C $REPO checkout -q $COMMIT
sed -i '/mirrors\./d; /^bpy==/d; /^deepspeed/d' $REPO/requirements.txt

echo "== Python environment"
uv venv -q --allow-existing --python 3.10 $W/venv
source $W/venv/bin/activate
uv pip install -q torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu124
uv pip install -q wheel ninja packaging pybind11 'setuptools<81'
uv pip install -q -r $REPO/requirements.txt
uv pip install -q 'setuptools<81' trimesh

# The texture pipeline imports bpy only for its own OBJ to GLB step, which isn't used.
echo "# Stub: make_item.py converts to GLB with trimesh." > $W/venv/lib/python3.10/site-packages/bpy.py
sed -i 's/from torchvision.transforms.functional_tensor import rgb_to_grayscale/from torchvision.transforms.functional import rgb_to_grayscale/' \
  $W/venv/lib/python3.10/site-packages/basicsr/data/degradations.py

echo "== CUDA extensions"
(cd $REPO/hy3dpaint/custom_rasterizer && uv pip install -q --no-build-isolation -e .)
(cd $REPO/hy3dpaint/DifferentiableRenderer && c++ -O3 -Wall -shared -std=c++11 -fPIC $(python -m pybind11 --includes) \
  mesh_inpaint_processor.cpp -o mesh_inpaint_processor$(python3.10-config --extension-suffix))
mkdir -p $REPO/hy3dpaint/ckpt
[ -f $REPO/hy3dpaint/ckpt/RealESRGAN_x4plus.pth ] || wget -q -P $REPO/hy3dpaint/ckpt \
  https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth

python -c "import torch, custom_rasterizer, pkg_resources; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
(cd $REPO/hy3dpaint && python -c "from DifferentiableRenderer.mesh_inpaint_processor import meshVerticeInpaint")
touch $W/.installed
echo "== installed (weights download on the first run)"
