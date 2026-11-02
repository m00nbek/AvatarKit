#!/usr/bin/env python3
"""Our RunPod pods: the clothes pod (Hunyuan3D 2.1 on a GPU, turning pictures into 3D
garments) and the build pod (CPUs, running the asset build so it doesn't load the Mac).

    python3 pod.py volume                      create the network volume (once)
    python3 pod.py up                          start the clothes pod and install what's missing
    python3 pod.py run <image> <out.glb> ...   3D model for each image (needs the clothes pod up)
    python3 pod.py down                        terminate the clothes pod; billing stops
    python3 pod.py build [--keep]              the asset build (build-assets.mjs) on the build pod:
                                               sources up, build, results back, pod terminated
    python3 pod.py status | ssh [build]

Both pods mount the same volume, which holds the Hunyuan install and weights, Node and the
build's Python packages, and the build's copy of the sources, so a new pod is ready in a few
minutes. Each pod deletes itself after MAX_HOURS as a safety net. generate.mjs drives the
clothes pod; see Docs/adding-clothes.md.

Settings in Tools/clothes/.env: RUNPOD_API_KEY (or the environment), CLOTHES_VOLUME_ID
(written by `volume`) and CLOTHES_DATA_CENTER (default US-NE-1).
"""

import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "../.."))
ENV = os.path.join(HERE, ".env")
API = "https://rest.runpod.io/v1"
USER_AGENT = "avatarkit-clothes/1.0"  # RunPod sits behind Cloudflare, which rejects Python's default
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
VOLUME_GB = 60
# Fastest first: waiting matters more than the hourly price. 48 GB or more, Ampere to
# Hopper: Hunyuan's PyTorch build (cu124) has no Blackwell kernels.
GPUS = ["NVIDIA H100 80GB HBM3", "NVIDIA H100 NVL", "NVIDIA H100 PCIe", "NVIDIA H200",
        "NVIDIA A100-SXM4-80GB", "NVIDIA A100 80GB PCIe", "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation",
        "NVIDIA L40", "NVIDIA RTX A6000", "NVIDIA A40"]
PODS = {
    "clothes": {"name": "avatarkit-clothes", "files": ["setup.sh", "make_item.py", "deadman.sh"], "setup": "setup.sh",
                "spec": {"computeType": "GPU", "gpuTypeIds": GPUS, "gpuTypePriority": "custom", "gpuCount": 1,
                         "containerDiskInGb": 40}},
    "build": {"name": "avatarkit-build", "files": ["setup-build.sh", "deadman.sh"], "setup": "setup-build.sh",
              "spec": {"computeType": "CPU", "cpuFlavorIds": ["cpu5c", "cpu3c"], "vcpuCount": 32,
                       "containerDiskInGb": 20}},
}
MAX_HOURS = 3
SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519")
# What the asset build reads, relative to the repo (keys and the bake-off's 700 MB stay home).
BUILD_SOURCES = ["Tools/", "Sources/AvatarKit/Resources/", ".assets-source/Tripo/", ".assets-source/Designer/",
                 ".assets-source/Universal Base Characters/", ".assets-source/Universal Animation Library/",
                 ".assets-source/Universal Animation Library 2/", ".assets-source/Universal Animation Library Source/"]
BUILD_EXCLUDES = ["node_modules/", ".env", "__pycache__/", ".DS_Store"]
# What comes back: the app's assets, and the build's intermediate files (rigged bodies,
# body renders, fitted items) that generate.mjs and later builds read.
BUILD_RESULTS = ["Sources/AvatarKit/Resources/", ".assets-source/Tripo/gen/"]
REMOTE = "/workspace/avatarkit"


# MARK: - Settings and API

def settings():
    values = {}
    if os.path.exists(ENV):
        for line in open(ENV):
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.strip().split("=", 1)
                values[key] = value
    values.setdefault("RUNPOD_API_KEY", os.environ.get("RUNPOD_API_KEY", ""))
    return values


def save_setting(key, value):
    lines = [line for line in open(ENV)] if os.path.exists(ENV) else []
    lines = [line for line in lines if not line.startswith(f"{key}=")] + [f"{key}={value}\n"]
    with open(ENV, "w") as out:
        out.writelines(lines)
    os.chmod(ENV, 0o600)


def api(method, path, body=None):
    key = settings()["RUNPOD_API_KEY"]
    if not key:
        sys.exit(f"Set RUNPOD_API_KEY in {ENV}")
    request = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                              "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        sys.exit(f"RunPod {method} {path}: HTTP {error.code} {error.read().decode(errors='replace')[:300]}")
    return json.loads(data) if data else None


def current_pod(kind="clothes"):
    name = PODS[kind]["name"]
    pods = [p for p in api("GET", "/pods") or [] if p.get("name") == name and p.get("desiredStatus") != "TERMINATED"]
    return api("GET", f"/pods/{pods[0]['id']}?includeMachine=true") if pods else None


def data_center():
    """Where the volume, and so every pod, lives: one with H100s in stock (CLOTHES_DATA_CENTER)."""
    return settings().get("CLOTHES_DATA_CENTER", "US-NE-1")


# MARK: - SSH

def ssh_options(pod):
    return ["-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "ServerAliveInterval=30"]


def ssh_args(pod, *options):
    return ["ssh", "-p", str(pod["portMappings"]["22"]), *ssh_options(pod), *options, f"root@{pod['publicIp']}"]


def remote(pod, command):
    """Runs a command on the pod, streaming its output; exits on failure."""
    result = subprocess.run(ssh_args(pod) + [command])
    if result.returncode:
        sys.exit(f"pod command failed ({result.returncode}): {command[:120]}")


def copy(pod, sources, destination):
    options = ["-P", str(pod["portMappings"]["22"]), *ssh_options(pod), "-q"]
    subprocess.run(["scp", *options, *sources, destination], check=True)


def sync(pod, sources, destination, *options):
    """rsync over the pod's SSH, from the repo: only what changed goes across."""
    shell = " ".join(["ssh", "-p", str(pod["portMappings"]["22"]), *ssh_options(pod)])
    subprocess.run(["rsync", "-az", "-e", shell, *options, *sources, destination], cwd=REPO, check=True)


def ready_pod(kind="clothes"):
    pod = current_pod(kind)
    if not pod:
        sys.exit(f"No {kind} pod running: python3 pod.py {'up' if kind == 'clothes' else 'build'}")
    return pod


# MARK: - Commands

def volume():
    old = settings().get("CLOTHES_VOLUME_ID")
    if old and api("GET", f"/networkvolumes/{old}").get("dataCenterId") == data_center():
        sys.exit(f"CLOTHES_VOLUME_ID is already a volume in {data_center()}")
    created = api("POST", "/networkvolumes", {"name": PODS["clothes"]["name"], "size": VOLUME_GB, "dataCenterId": data_center()})
    save_setting("CLOTHES_VOLUME_ID", created["id"])
    print(f"volume {created['id']}: {VOLUME_GB} GB in {data_center()} (~${VOLUME_GB * 0.07:.2f}/month)")
    if old:
        print(f"the old volume {old} is still there (~${VOLUME_GB * 0.07:.2f}/month); delete it on runpod.io when it's not needed")


def up(kind="clothes"):
    """Starts the pod (unless it's running), with the deadman and its setup script."""
    config = PODS[kind]
    pod = current_pod(kind)
    if not pod:
        volume_id = settings().get("CLOTHES_VOLUME_ID")
        if not volume_id:
            sys.exit("No volume yet: python3 pod.py volume")
        public_key = open(SSH_KEY + ".pub").read().strip()
        pod = api("POST", "/pods", {
            "name": config["name"], "imageName": IMAGE, "cloudType": "SECURE", **config["spec"],
            "networkVolumeId": volume_id, "volumeMountPath": "/workspace",
            "ports": ["22/tcp"], "env": {"PUBLIC_KEY": public_key},
        })
        print(f"{kind} pod {pod['id']} starting (${pod.get('costPerHr')}/hr)", flush=True)
    for _ in range(90):
        pod = api("GET", f"/pods/{pod['id']}?includeMachine=true")
        if pod.get("publicIp") and "22" in (pod.get("portMappings") or {}):
            break
        time.sleep(10)
    else:
        sys.exit("pod didn't become reachable in 15 minutes; check it on runpod.io")
    machine = pod.get("machine") or {}
    print(f"{kind} pod {pod['id']} up: {machine.get('gpuTypeId') or machine.get('cpuFlavorId') or kind}, "
          f"${pod.get('costPerHr')}/hr", flush=True)
    time.sleep(5)
    copy(pod, [os.path.join(HERE, name) for name in config["files"]], f"root@{pod['publicIp']}:/workspace/")
    # Safety net: the pod deletes itself after MAX_HOURS, even if nobody runs `down`.
    remote(pod, f"setsid nohup bash /workspace/deadman.sh {MAX_HOURS} > /dev/null 2>&1 < /dev/null &")
    remote(pod, f"bash /workspace/{config['setup']}")
    return pod


def run(args):
    if not args or len(args) % 2:
        sys.exit("usage: pod.py run <image> <out.glb> [<image> <out.glb> ...]")
    pod = ready_pod()
    host = f"root@{pod['publicIp']}"
    job = f"/workspace/jobs/{time.strftime('%Y%m%d-%H%M%S')}"
    remote(pod, f"mkdir -p {job}")
    pairs = list(zip(args[0::2], args[1::2]))
    # Numbered names: outfits' images are all called image.png, each in its own folder.
    for i, (image, _) in enumerate(pairs):
        copy(pod, [image], f"{host}:{job}/in{i}{os.path.splitext(image)[1]}")
    remote_args = " ".join(f"{job}/in{i}{shlex.quote(os.path.splitext(image)[1])} {job}/out{i}.glb" for i, (image, _) in enumerate(pairs))
    remote(pod, f"cd /workspace && HF_HOME=/workspace/hf HY3DGEN_MODELS=/workspace/hy3dgen "
                f"PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True venv/bin/python make_item.py {remote_args}")
    for i, (_, out) in enumerate(pairs):
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        copy(pod, [f"{host}:{job}/out{i}.glb"], out)
        print(f"saved {out}")


def build(keep=False):
    """The asset build on the build pod: sources up, `npm run build-assets`, results back."""
    pod = up("build")
    host = f"root@{pod['publicIp']}"
    try:
        start = time.time()
        remote(pod, f"mkdir -p {shlex.quote(REMOTE)}")
        sync(pod, [f"./{path}" for path in BUILD_SOURCES], f"{host}:{REMOTE}/", "--relative", "--delete",
             *[f"--exclude={pattern}" for pattern in BUILD_EXCLUDES])
        print(f"sources synced in {time.time() - start:.0f} s", flush=True)
        # node_modules is reinstalled only when the lock file changes.
        remote(pod, f"cd {REMOTE}/Tools && export PATH=/workspace/node/bin:/workspace/buildenv/bin:$PATH && "
                    f"(sha256sum -c .lock.sha256 > /dev/null 2>&1 || (npm ci --no-audit --no-fund --loglevel=error && "
                    f"sha256sum package-lock.json > .lock.sha256)) && npm run build-assets")
        for path in BUILD_RESULTS:
            # --update: never overwrite what changed here meanwhile (generate.mjs's state.json).
            sync(pod, [f"{host}:{REMOTE}/{path}"], path, "--update")
        print(f"build done in {time.time() - start:.0f} s; results synced back", flush=True)
    finally:
        if not keep:
            down("build")


def down(kind="clothes"):
    pod = current_pod(kind)
    if not pod:
        print(f"no {kind} pod running")
        return
    api("DELETE", f"/pods/{pod['id']}")
    print(f"terminated {kind} pod {pod['id']}; the volume keeps its files")


def status():
    volume_id = settings().get("CLOTHES_VOLUME_ID") or "none yet"
    for kind in PODS:
        pod = current_pod(kind)
        if pod:
            machine = pod.get("machine") or {}
            print(f"{kind} pod {pod['id']}: {machine.get('gpuTypeId') or machine.get('cpuFlavorId')}, ${pod.get('costPerHr')}/hr")
        else:
            print(f"no {kind} pod running")
    print(f"volume {volume_id} in {data_center()}")


def main():
    command, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if command == "volume":
        volume()
    elif command == "up":
        up()
    elif command == "run":
        run(args)
    elif command == "down":
        down(args[0] if args else "clothes")
    elif command == "build":
        build(keep="--keep" in args)
    elif command == "status":
        status()
    elif command == "ssh":
        os.execvp("ssh", ssh_args(ready_pod(args[0] if args else "clothes"), "-t"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
