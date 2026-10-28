# Phase 0 spike: DGX Spark toolchain

ADR-013 requires this spike before any GPU or container dependency is pinned. It can only be run on the DGX Spark itself, so this file is the procedure and the place to record results; the cloud CI cannot fill it in.

| Check | Command | Expected | Result (fill in on the host) |
|---|---|---|---|
| DGX OS and kernel | `cat /etc/os-release; uname -a` | DGX OS, aarch64 | |
| Driver and CUDA | `nvidia-smi; nvcc --version` | Driver and CUDA versions recorded | |
| Container toolkit and CDI | `nvidia-ctk cdi generate --output=$HOME/.config/cdi/nvidia.yaml && nvidia-ctk cdi list` | `nvidia.com/gpu=all` listed | |
| Rootless Podman | `podman info --format '{{.Host.Security.Rootless}}'` | `true` | |
| GPU in a rootless container | `podman run --rm --device nvidia.com/gpu=all docker.io/nvidia/cuda:13.0.0-base-ubuntu24.04 nvidia-smi` | GPU listed | |
| Quadlet | `systemctl --user daemon-reload && systemctl --user list-unit-files 'sdp-*'` | units generated from `infrastructure/quadlet/` | |
| NAS mount and throughput | `fio --name=seq --rw=write --bs=1M --size=4G --directory=/mnt/nas/sdp/scratch` then `--rw=read` | Sustained MB/s recorded | |
| NAS free space | `df -h /mnt/nas/sdp` | Allocation plan of SDD 0.2.0 §8.3 fits | |
| cuML on ARM64 | `python -c "import cuml; print(cuml.__version__)"` inside the runner image | Import works, or the gap is recorded (risk §18) | |
| Git signing | `git -C ledger-checkout commit -S --allow-empty -m test && git verify-commit HEAD` | Signature verifies | |

Run `just spike` on the host to execute the non-destructive checks and print a report you can paste below.

## Results

Not yet run.
