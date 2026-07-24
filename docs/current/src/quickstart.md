# Quick Start

Pick the path that matches where you are running.

## A. Air-gap RKE2 (primary product path)

On a control-plane node with kubeconfig and (optionally) a Zarf package staged:

```bash
# Session setup
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
export PATH="$PATH:/var/lib/rancher/rke2/bin"

# Stage small converge tree (~400 KB) or full package; then:
sudo env CONVERGE_CREDS_FILE=/dev/shm/s3-creds \
  bash zarf/scripts/converge-node.sh verify

sudo env CONVERGE_CREDS_FILE=/dev/shm/s3-creds \
  bash zarf/scripts/converge-node.sh apply

sudo bash zarf/scripts/verify-s3-datapath.sh
```

S3 creds file example:

```bash
umask 077
cat > /dev/shm/s3-creds <<'EOF'
S3_ENDPOINT=https://...
S3_BUCKET=your-bucket
S3_REGION=us-east-1
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
EOF
```

Details: [Zarf air-gap releases](./delivery/zarf.md) · [Converge](./delivery/converge.md) · in-repo `zarf/AIRGAP-*.md`.

## B. AWS provision + deploy

From a developer machine with devenv / tofu / ansible:

```bash
# Provision RKE2 nodes + networking (OpenTofu)
devenv tasks run aws:provision   # or: cd infra/aws/tofu && tofu apply

# Deploy stack (Ansible → RKE2 + Zarf)
devenv tasks run aws:deploy
```

Details: [AWS & infrastructure](./delivery/infra.md) · `infra/README.md` · `infra/aws/README.md`.

## C. Local laptop (devenv lab)

```bash
devenv up
# Flink UI :8081 · Iceberg browser :5050 · MinIO :9011 · Polaris :8181
```

Optional local K8s:

```bash
devenv tasks run k8s:provision
devenv tasks run k8s:deploy-dask
devenv tasks run k8s:forward
```

Details: [Laptop development](./workloads/laptop-dev.md) · `infra/LOCAL.md`.

## D. Build Flink pipeline toolkit

```bash
cd flink-cyber
mvn clean install -DskipTests
# Cloudera parcel/CSD modules are not in the reactor
```

See `flink-cyber/PACKAGING.md`.
