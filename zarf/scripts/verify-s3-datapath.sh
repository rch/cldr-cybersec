#!/usr/bin/env bash
# verify-s3-datapath.sh — prove the *deployed* panel/Dask stack can reach S3 and
# read the configured dataset (marker + span parquet already in place).
#
# Why this exists: Kubernetes readiness is TCP-only for otel-navigator. Pods can
# be Ready while S3_BUCKET is blank, the endpoint is wrong, auth fails, or the
# bucket has no spans. This check runs *inside* a cluster pod so it uses the
# same env/creds the app uses — secrets never appear on argv/ps.
#
# Usage (control-plane or any host with kubeconfig):
#   sudo bash zarf/scripts/verify-s3-datapath.sh
#   bash verify-s3-datapath.sh --json
#   bash verify-s3-datapath.sh --min-parquet 1
#
# Exit codes:
#   0  — bucket reachable, marker OK, ≥1 span parquet readable (or --allow-empty)
#   1  — config/auth/data failure (see printed diagnosis)
#   2  — preconditions (no kubectl / no Ready pod to exec into)
#
# Env (optional overrides; default = read from panel-viz ConfigMap + pod env):
#   KUBECONFIG, S3_BUCKET (only if CM empty — prefer CM), --allow-empty
set -euo pipefail

JSON=0
ALLOW_EMPTY=0
MIN_PARQUET=1
QUIET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --json) JSON=1; shift ;;
    --allow-empty) ALLOW_EMPTY=1; MIN_PARQUET=0; shift ;;
    --min-parquet) MIN_PARQUET="${2:?}"; shift 2 ;;
    --quiet|-q) QUIET=1; shift ;;
    -h|--help)
      sed -n '2,25p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/rke2/rke2.yaml}"

_log() { [ "$QUIET" = 1 ] || echo "$*"; }
_err() { echo "$*" >&2; }

# Resolve kubectl (RKE2 layout, zarf tools, PATH)
kc() {
  if [ -x /var/lib/rancher/rke2/bin/kubectl ]; then
    /var/lib/rancher/rke2/bin/kubectl --kubeconfig "$KUBECONFIG" "$@"
  elif command -v kubectl >/dev/null 2>&1; then
    kubectl --kubeconfig "$KUBECONFIG" "$@"
  elif command -v zarf >/dev/null 2>&1; then
    zarf tools kubectl --kubeconfig "$KUBECONFIG" "$@"
  else
    _err "❌ no kubectl (checked RKE2 bin, PATH, zarf tools)"
    exit 2
  fi
}

# --------------------------------------------------------------------------- #
# 1) Configured location (cluster ConfigMap — what the app actually got)
# --------------------------------------------------------------------------- #
_log "== configured S3 (panel-viz ConfigMap) =="
CM_JSON=$(kc -n panel-viz get configmap otel-navigator-config -o json 2>/dev/null || true)
if [ -z "$CM_JSON" ]; then
  _err "❌ otel-navigator-config ConfigMap missing in panel-viz — deploy panel-viz first"
  exit 2
fi
CFG_BUCKET=$(printf '%s' "$CM_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin).get("data") or {}; print((d.get("S3_BUCKET") or "").strip())')
CFG_PATH=$(printf '%s' "$CM_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin).get("data") or {}; print((d.get("OTEL_DATA_PATH") or "").strip())')
CFG_REGION=$(printf '%s' "$CM_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin).get("data") or {}; print((d.get("AWS_REGION") or "us-east-1").strip())')

_log "  S3_BUCKET       = ${CFG_BUCKET:-<empty>}"
_log "  OTEL_DATA_PATH  = ${CFG_PATH:-<empty>}"
_log "  AWS_REGION      = ${CFG_REGION}"

if [ -z "$CFG_BUCKET" ] || [ "$CFG_BUCKET" = "s3:///" ] || [[ "$CFG_PATH" == "s3:///"* ]] || [[ "$CFG_PATH" == "s3:///" ]]; then
  _err "❌ blank/unrendered S3_BUCKET or OTEL_DATA_PATH=s3:/// — redeploy panel-viz with S3_* (see converge T5.otel-navigator)"
  exit 1
fi

# Secret presence only (never print values)
if ! kc -n panel-viz get secret otel-navigator-credentials >/dev/null 2>&1; then
  _err "❌ otel-navigator-credentials Secret missing"
  exit 1
fi
_log "  credentials Secret: present"

# --------------------------------------------------------------------------- #
# 2) Pick an in-cluster exec target that has s3fs + the same creds as the app
# --------------------------------------------------------------------------- #
EXEC_NS=""
EXEC_TARGET=""  # deploy/name or pod/name
EXEC_C=""

if kc -n panel-viz get deploy otel-navigator >/dev/null 2>&1; then
  ready=$(kc -n panel-viz get pods -l app=otel-navigator \
    -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' 2>/dev/null | grep -c True || true)
  if [ "${ready:-0}" -ge 1 ]; then
    EXEC_NS=panel-viz
    EXEC_TARGET="deploy/otel-navigator"
    EXEC_C="-c otel-navigator"
  fi
fi
if [ -z "$EXEC_TARGET" ] && kc -n dask get deploy -l dask.org/component=scheduler -o name 2>/dev/null | head -1 | grep -q .; then
  EXEC_NS=dask
  # Prefer the standard name; fall back to first scheduler pod
  if kc -n dask get deploy cybersec-dask-scheduler >/dev/null 2>&1; then
    EXEC_TARGET="deploy/cybersec-dask-scheduler"
  else
    EXEC_TARGET=$(kc -n dask get pods -l dask.org/component=scheduler -o name 2>/dev/null | head -1)
  fi
fi
if [ -z "$EXEC_TARGET" ]; then
  _err "❌ no Ready otel-navigator or Dask scheduler to exec into"
  exit 2
fi
_log "== in-cluster probe via $EXEC_NS/$EXEC_TARGET ${EXEC_C:-} =="

# --------------------------------------------------------------------------- #
# 3) In-pod check: auth + marker + span parquet (mirrors otel-navigator loader)
# --------------------------------------------------------------------------- #
# MIN_PARQUET / ALLOW_EMPTY / CFG_BUCKET injected via env on the exec so we never
# embed operator secrets; the pod already has AWS_* and S3_ENDPOINT.
# shellcheck disable=SC2086
RESULT=$(kc -n "$EXEC_NS" exec $EXEC_TARGET $EXEC_C -- env \
  CHECK_BUCKET="$CFG_BUCKET" \
  CHECK_MIN_PARQUET="$MIN_PARQUET" \
  CHECK_ALLOW_EMPTY="$ALLOW_EMPTY" \
  CHECK_CFG_PATH="$CFG_PATH" \
  python3 - <<'PY'
import json, os, sys, traceback

def die(code, msg, **extra):
    out = {"ok": False, "error": msg, **extra}
    print(json.dumps(out))
    sys.exit(code)

bucket = (os.environ.get("CHECK_BUCKET") or os.environ.get("S3_BUCKET") or "").strip()
cfg_path = (os.environ.get("CHECK_CFG_PATH") or os.environ.get("OTEL_DATA_PATH") or "").strip()
min_pq = int(os.environ.get("CHECK_MIN_PARQUET") or "1")
allow_empty = os.environ.get("CHECK_ALLOW_EMPTY") == "1"

# Presence / lengths only — never print secret material
akid = os.environ.get("AWS_ACCESS_KEY_ID") or ""
secret = os.environ.get("AWS_SECRET_ACCESS_KEY") or ""
endpoint = (os.environ.get("S3_ENDPOINT") or "").strip()
region = (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1").strip()
pod_bucket = (os.environ.get("S3_BUCKET") or "").strip()

info = {
    "ok": True,
    "bucket_configmap": bucket,
    "bucket_pod_env": pod_bucket,
    "otel_data_path": cfg_path,
    "endpoint_set": bool(endpoint),
    "endpoint_host": endpoint.split("://")[-1].split("/")[0] if endpoint else "",
    "region": region,
    "aws_access_key_len": len(akid),
    "aws_secret_key_len": len(secret),
}

if not bucket:
    die(1, "S3_BUCKET empty in check env and pod", **info)
if len(akid) == 0 or len(secret) == 0:
    die(1, "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY empty in pod env — redeploy with S3 creds", **info)
if pod_bucket and pod_bucket != bucket:
    info["warning"] = f"pod S3_BUCKET={pod_bucket!r} != ConfigMap {bucket!r}"

try:
    import s3fs
except ImportError as e:
    die(1, f"s3fs not installed in probe image: {e}", **info)

# Match app / RUNBOOK: path-style when a custom endpoint is set (MinIO / gateway).
kw = {
    "key": akid,
    "secret": secret,
    "client_kwargs": {"endpoint_url": endpoint or None, "region_name": region},
}
if endpoint:
    kw["config_kwargs"] = {"s3": {"addressing_style": "path"}}
# Session token for temporary IAM creds
tok = os.environ.get("AWS_SESSION_TOKEN") or ""
if tok:
    kw["token"] = tok

try:
    fs = s3fs.S3FileSystem(**kw)
except Exception as e:
    die(1, f"S3FileSystem init failed: {e}", **info)

# --- reach bucket ---
try:
    # ls root of bucket (auth + existence)
    fs.ls(bucket)
    info["bucket_reachable"] = True
except Exception as e:
    die(1, f"cannot list bucket {bucket!r}: {type(e).__name__}: {e}", **info)

# --- active dataset marker (same as otel-navigator.get_active_dataset) ---
marker_key = f"{bucket}/_active_dataset.json"
try:
    if not fs.exists(marker_key):
        die(1, f"marker missing: s3://{marker_key} — generate spans or copy marker "
               f"(OTEL_Data_Generator notebook / generate-otel-data.py)", **info)
    with fs.open(marker_key, "r") as f:
        marker = json.load(f)
except Exception as e:
    die(1, f"cannot read marker s3://{marker_key}: {e}", **info)

dataset = marker.get("dataset") or marker.get("prefix")
if not dataset:
    die(1, "marker has no 'dataset' key", marker=marker, **info)

info["marker"] = {
    "dataset": dataset,
    "phase": marker.get("phase"),
    "total_spans": marker.get("total_spans", marker.get("span_count")),
    "updated_at": marker.get("updated_at"),
}
info["dataset_path"] = f"s3://{bucket}/{dataset}/"

# --- span parquet under the active dataset (app loads …/spans/) ---
# Layouts seen in field: {dataset}/spans/date=…/hour=…/*.parquet
#                        {dataset}/spans/shard=…/date=…/*.parquet
patterns = [
    f"{bucket}/{dataset}/spans/**/*.parquet",
    f"{bucket}/{dataset}/**/spans/**/*.parquet",
    f"{bucket}/{dataset}/**/*.parquet",
]
files = []
for pat in patterns:
    try:
        found = fs.glob(pat)
    except Exception:
        found = []
    if found:
        files = list(found)
        info["glob_pattern"] = pat
        break

info["parquet_count"] = len(files)
info["parquet_sample"] = files[:5]

if len(files) < min_pq and not allow_empty:
    die(1,
        f"found {len(files)} parquet under s3://{bucket}/{dataset}/ "
        f"(need ≥{min_pq}) — spans not present or wrong prefix",
        **info)

# --- open first object (read path, not just list) ---
if files:
    sample = files[0]
    try:
        with fs.open(sample, "rb") as f:
            head = f.read(64)
        info["sample_readable"] = True
        info["sample_key"] = sample
        info["sample_head_bytes"] = len(head)
        # Optional: parquet magic
        if head[:4] == b"PAR1" or b"PAR1" in head:
            info["sample_looks_like_parquet"] = True
    except Exception as e:
        die(1, f"list OK but cannot read sample {sample}: {e}", **info)

# --- optional: dask/pyarrow smoke if available (same stack as workers) ---
if files and os.environ.get("CHECK_DEEP") == "1":
    try:
        import pyarrow.parquet as pq
        with fs.open(files[0], "rb") as f:
            t = pq.read_table(f, columns=None)
        info["deep_rows"] = t.num_rows
        info["deep_cols"] = t.column_names[:12]
    except Exception as e:
        info["deep_error"] = str(e)

print(json.dumps(info))
sys.exit(0)
PY
) || {
  # kubectl exec may wrap non-zero; try to still show JSON if present
  _err "❌ in-cluster probe failed (kubectl exec rc=$?)"
  if [ -n "${RESULT:-}" ]; then
    echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
  fi
  exit 1
}

if [ "$JSON" = 1 ]; then
  echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
else
  echo "$RESULT" | python3 -c '
import json,sys
d=json.load(sys.stdin)
ok=d.get("ok", False)
print("== result ==")
print("  ok:                 ", ok)
print("  bucket:             ", d.get("bucket_configmap"))
print("  endpoint:           ", d.get("endpoint_host") or "(AWS default)")
print("  access_key_len:     ", d.get("aws_access_key_len"))
print("  secret_key_len:     ", d.get("aws_secret_key_len"))
print("  bucket_reachable:   ", d.get("bucket_reachable"))
m=d.get("marker") or {}
print("  active dataset:     ", m.get("dataset"))
print("  marker phase:       ", m.get("phase"))
print("  marker spans:       ", m.get("total_spans"))
print("  dataset_path:       ", d.get("dataset_path"))
print("  parquet_count:      ", d.get("parquet_count"))
print("  sample_readable:    ", d.get("sample_readable"))
if d.get("parquet_sample"):
    print("  sample keys:")
    for k in d["parquet_sample"][:5]:
        print("   -", k)
if d.get("error"):
    print("  ERROR:", d["error"])
    sys.exit(1)
if not ok:
    sys.exit(1)
print()
print("✔ S3 datapath OK — app can reach configured bucket and read span parquet")
'
fi

# Propagate python exit via JSON ok field
echo "$RESULT" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("ok") else 1)'
