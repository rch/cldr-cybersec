#!/usr/bin/env bash
# Monitor AWS cluster deployment (ansible + RKE2 + Dask)
# Usage: ./scripts/monitor_deploy.sh [--watch]
#
# Run in a separate terminal while aws:deploy is running:
#   ./scripts/monitor_deploy.sh --watch

set -euo pipefail

TOFU_DIR="infra/aws/tofu"
REFRESH_INTERVAL="${REFRESH_INTERVAL:-10}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

get_cluster_info() {
    cd "$TOFU_DIR" 2>/dev/null || return 1

    BASTION_IP=$(tofu output -raw bastion_public_ip 2>/dev/null || echo "")
    CONTROL_IP=$(tofu output -json control_plane_private_ips 2>/dev/null | jq -r '.[0] // empty' || echo "")
    REGION=$(tofu output -json cluster_info 2>/dev/null | jq -r '.region // "us-east-1"')

    if [ -z "$BASTION_IP" ] || [ -z "$CONTROL_IP" ]; then
        return 1
    fi
    return 0
}

run_remote() {
    local cmd="$1"
    ssh -i ~/.ssh/cybersec-dask.pem \
        -o ProxyCommand="ssh -i ~/.ssh/cybersec-dask.pem -W %h:%p -o StrictHostKeyChecking=no ec2-user@$BASTION_IP" \
        -o StrictHostKeyChecking=no \
        -o ConnectTimeout=5 \
        ec2-user@"$CONTROL_IP" "$cmd" 2>/dev/null
}

get_phase() {
    # Detect current phase based on processes
    if pgrep -f "ansible-playbook" >/dev/null 2>&1; then
        # Try to detect which playbook
        local playbook=$(ps aux | grep "ansible-playbook" | grep -v grep | head -1 | sed 's/.*playbooks\/\([^.]*\).yml.*/\1/' || echo "unknown")
        echo "ansible:$playbook"
    elif pgrep -f "tofu apply" >/dev/null 2>&1; then
        echo "provisioning"
    else
        echo "idle"
    fi
}

check_bastion() {
    if [ -z "$BASTION_IP" ]; then
        echo -e "  ${RED}✗${NC} Bastion (not provisioned)"
        return 1
    fi

    if nc -z -w2 "$BASTION_IP" 22 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} Bastion ($BASTION_IP)"
        return 0
    else
        echo -e "  ${YELLOW}⟳${NC} Bastion ($BASTION_IP) - waiting for SSH"
        return 1
    fi
}

check_control_plane() {
    if [ -z "$CONTROL_IP" ]; then
        echo -e "  ${RED}✗${NC} Control Plane (not provisioned)"
        return 1
    fi

    # Check SSH through bastion
    if run_remote "true" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} Control Plane ($CONTROL_IP)"
        return 0
    else
        echo -e "  ${YELLOW}⟳${NC} Control Plane ($CONTROL_IP) - waiting for SSH"
        return 1
    fi
}

check_rke2() {
    local status=$(run_remote "systemctl is-active rke2-server 2>/dev/null" || echo "unknown")

    case "$status" in
        active)
            echo -e "  ${GREEN}✓${NC} RKE2 Server (active)"
            return 0
            ;;
        activating)
            echo -e "  ${YELLOW}⟳${NC} RKE2 Server (starting...)"
            return 1
            ;;
        inactive|failed)
            echo -e "  ${YELLOW}○${NC} RKE2 Server (not started)"
            return 1
            ;;
        *)
            echo -e "  ${YELLOW}?${NC} RKE2 Server (unknown)"
            return 1
            ;;
    esac
}

check_nodes() {
    local kubectl="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"
    local nodes=$(run_remote "$kubectl get nodes --no-headers 2>/dev/null" || echo "")

    if [ -z "$nodes" ]; then
        echo -e "  ${YELLOW}○${NC} K8s Nodes (API not ready)"
        return 1
    fi

    local total=$(echo "$nodes" | wc -l | tr -d ' ')
    local ready=$(echo "$nodes" | grep -c " Ready" || true)
    local notready=$(echo "$nodes" | grep -c "NotReady" || true)

    if [ "$notready" -gt 0 ]; then
        echo -e "  ${YELLOW}⟳${NC} K8s Nodes ($ready/$total ready, $notready joining)"
    elif [ "$ready" -eq "$total" ]; then
        echo -e "  ${GREEN}✓${NC} K8s Nodes ($ready/$total ready)"
    else
        echo -e "  ${YELLOW}○${NC} K8s Nodes ($ready/$total ready)"
    fi
}

check_dask_operator() {
    local kubectl="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"
    local pods=$(run_remote "$kubectl get pods -n dask-operator --no-headers 2>/dev/null" || echo "")

    if [ -z "$pods" ]; then
        echo -e "  ${YELLOW}○${NC} Dask Operator (not deployed)"
        return 1
    fi

    local running=$(echo "$pods" | grep -c "Running" || true)
    local pending=$(echo "$pods" | grep -c "Pending\|ContainerCreating" || true)
    local total=$(echo "$pods" | wc -l | tr -d ' ')

    if [ "$pending" -gt 0 ]; then
        echo -e "  ${YELLOW}⟳${NC} Dask Operator ($running/$total running)"
    elif [ "$running" -eq "$total" ]; then
        echo -e "  ${GREEN}✓${NC} Dask Operator ($running pods)"
    else
        echo -e "  ${RED}!${NC} Dask Operator ($running/$total running)"
    fi
}

check_dask_cluster() {
    local kubectl="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"
    local clusters=$(run_remote "$kubectl get daskclusters -A --no-headers 2>/dev/null" || echo "")

    if [ -z "$clusters" ]; then
        echo -e "  ${YELLOW}○${NC} Dask Cluster (not created)"
        return 1
    fi

    # Get worker count
    local workers=$(run_remote "$kubectl get pods -n dask -l dask.org/component=worker --no-headers 2>/dev/null" | wc -l | tr -d ' ')
    local running_workers=$(run_remote "$kubectl get pods -n dask -l dask.org/component=worker --no-headers 2>/dev/null" | grep -c "Running" || true)
    workers=${workers:-0}

    if [ "$workers" -eq 0 ]; then
        echo -e "  ${YELLOW}○${NC} Dask Cluster (0 workers)"
    elif [ "$running_workers" -lt "$workers" ]; then
        echo -e "  ${YELLOW}⟳${NC} Dask Cluster ($running_workers/$workers workers ready)"
    else
        echo -e "  ${GREEN}✓${NC} Dask Cluster ($running_workers workers)"
    fi
}

check_ngrok() {
    local kubectl="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"
    local pods=$(run_remote "$kubectl get pods -n ngrok-system --no-headers 2>/dev/null" || echo "")

    if [ -z "$pods" ]; then
        echo -e "  ${YELLOW}○${NC} ngrok Operator (not deployed)"
        return 1
    fi

    local running=$(echo "$pods" | grep -c "Running" || true)

    if [ "$running" -gt 0 ]; then
        # Check for ingresses
        local ingresses=$(run_remote "$kubectl get ingress -A -l app.kubernetes.io/managed-by=ngrok-operator --no-headers 2>/dev/null" | wc -l | tr -d ' ')
        echo -e "  ${GREEN}✓${NC} ngrok Operator ($ingresses ingresses)"
    else
        echo -e "  ${YELLOW}⟳${NC} ngrok Operator (starting)"
    fi
}

show_diagnostics() {
    local bastion_ok="$1"
    local control_ok="$2"

    echo ""
    echo -e "${CYAN}Diagnostics:${NC}"

    # SSH connectivity issues
    if [ "$bastion_ok" -eq 0 ]; then
        echo -e "  ${YELLOW}Bastion unreachable:${NC}"
        echo "    ssh -i ~/.ssh/cybersec-dask.pem ec2-user@$BASTION_IP"
        echo "    # Check: security group, instance state, key permissions"
        return
    fi

    if [ "$control_ok" -eq 0 ]; then
        echo -e "  ${YELLOW}Control plane unreachable via bastion:${NC}"
        echo "    # SSH to bastion first:"
        echo "    ssh -i ~/.ssh/cybersec-dask.pem ec2-user@$BASTION_IP"
        echo "    # Then from bastion:"
        echo "    ssh ec2-user@$CONTROL_IP"
        return
    fi

    # Check for ansible failures
    local ansible_log=$(find /tmp -maxdepth 1 -name "ansible*.log" -mmin -30 2>/dev/null | head -1)
    if [ -n "$ansible_log" ]; then
        local failures=$(grep -c "FAILED\|UNREACHABLE" "$ansible_log" 2>/dev/null || true)
        if [ "$failures" -gt 0 ]; then
            echo -e "  ${RED}Ansible failures detected:${NC}"
            echo "    tail -100 $ansible_log | grep -A5 'FAILED\|UNREACHABLE'"
            return
        fi
    fi

    # Build SSH command for copy-paste
    local ssh_cmd="ssh -i ~/.ssh/cybersec-dask.pem -o ProxyCommand=\"ssh -i ~/.ssh/cybersec-dask.pem -W %h:%p ec2-user@$BASTION_IP\" ec2-user@$CONTROL_IP"

    # RKE2 issues
    local rke2_status=$(run_remote "systemctl is-active rke2-server 2>/dev/null" || echo "unknown")
    if [ "$rke2_status" = "failed" ] || [ "$rke2_status" = "inactive" ]; then
        echo -e "  ${YELLOW}RKE2 not running:${NC}"
        echo "    # Check RKE2 logs:"
        echo "    $ssh_cmd 'sudo journalctl -u rke2-server -n 50 --no-pager'"
        return
    fi

    # K8s API issues
    local kubectl="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"
    local api_ok=$(run_remote "$kubectl cluster-info 2>/dev/null" || echo "")
    if [ -z "$api_ok" ]; then
        echo -e "  ${YELLOW}K8s API not responding:${NC}"
        echo "    # Check API server:"
        echo "    $ssh_cmd 'sudo crictl ps | grep kube-apiserver'"
        echo "    $ssh_cmd 'sudo journalctl -u rke2-server -n 30 --no-pager'"
        return
    fi

    # Pod issues - check for crash loops or pending
    local problem_pods=$(run_remote "$kubectl get pods -A --no-headers 2>/dev/null" | grep -E "CrashLoop|Error|ImagePull|Pending" || true)
    if [ -n "$problem_pods" ]; then
        echo -e "  ${YELLOW}Pods with issues:${NC}"
        echo "$problem_pods" | head -5 | while read -r line; do
            echo "    $line"
        done
        local ns=$(echo "$problem_pods" | head -1 | awk '{print $1}')
        local pod=$(echo "$problem_pods" | head -1 | awk '{print $2}')
        echo "    # Investigate first problem pod:"
        echo "    $ssh_cmd '$kubectl describe pod $pod -n $ns'"
        echo "    $ssh_cmd '$kubectl logs $pod -n $ns'"
        return
    fi

    echo -e "  ${GREEN}No issues detected${NC}"
}

check_jupyterhub() {
    local kubectl="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"
    local pods=$(run_remote "$kubectl get pods -n jupyterhub --no-headers 2>/dev/null" || echo "")

    if [ -z "$pods" ]; then
        echo -e "  ${YELLOW}○${NC} JupyterHub (not deployed)"
        return 1
    fi

    local running=$(echo "$pods" | grep -c "Running" || true)
    local total=$(echo "$pods" | wc -l | tr -d ' ')

    if [ "$running" -eq "$total" ]; then
        echo -e "  ${GREEN}✓${NC} JupyterHub ($running pods)"
    else
        echo -e "  ${YELLOW}⟳${NC} JupyterHub ($running/$total running)"
    fi
}

show_status() {
    clear
    local phase=$(get_phase)

    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║              AWS Cluster Deploy Monitor                    ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    # Phase indicator
    echo -n "Phase: "
    case "$phase" in
        provisioning)
            echo -e "${YELLOW}⟳ Provisioning infrastructure...${NC}"
            ;;
        ansible:*)
            local playbook="${phase#ansible:}"
            echo -e "${YELLOW}⟳ Running ansible ($playbook)...${NC}"
            ;;
        idle)
            echo -e "${GREEN}◉ Idle${NC}"
            ;;
    esac

    if ! get_cluster_info; then
        echo ""
        echo -e "${RED}Cluster not provisioned. Run 'devenv tasks run aws:provision' first.${NC}"
        echo ""
        echo -e "${BLUE}─────────────────────────────────────────────────────────────${NC}"
        echo "Refresh: ${REFRESH_INTERVAL}s | Ctrl+C to exit"
        return
    fi

    echo "Region: $REGION"
    echo ""

    echo -e "${CYAN}Infrastructure:${NC}"
    local bastion_ok=0
    local control_ok=0
    check_bastion && bastion_ok=1 || true
    check_control_plane && control_ok=1 || true

    if [ "$control_ok" -eq 0 ]; then
        show_diagnostics "$bastion_ok" "$control_ok"
        echo ""
        echo -e "${BLUE}─────────────────────────────────────────────────────────────${NC}"
        echo "Refresh: ${REFRESH_INTERVAL}s | Ctrl+C to exit"
        return
    fi

    echo ""
    echo -e "${CYAN}Kubernetes:${NC}"
    check_rke2 || true
    check_nodes || true

    echo ""
    echo -e "${CYAN}Workloads:${NC}"
    check_dask_operator || true
    check_dask_cluster || true
    check_ngrok || true
    check_jupyterhub || true

    # Show diagnostics if something isn't fully healthy
    show_diagnostics "$bastion_ok" "$control_ok"

    echo ""
    echo -e "${BLUE}─────────────────────────────────────────────────────────────${NC}"
    echo "Refresh: ${REFRESH_INTERVAL}s | Ctrl+C to exit"
}

# Main
if [ "${1:-}" = "--watch" ] || [ "${1:-}" = "-w" ]; then
    while true; do
        show_status
        sleep "$REFRESH_INTERVAL"
    done
else
    show_status
fi
