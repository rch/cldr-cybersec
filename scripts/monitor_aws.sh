#!/usr/bin/env bash
# Monitor AWS infrastructure operations (provision, destroy, etc.)
# Usage: ./scripts/monitor_aws.sh [--watch]
#
# Run in a separate terminal while aws:provision or aws:destroy is running:
#   ./scripts/monitor_aws.sh --watch

set -euo pipefail

TOFU_DIR="infra/aws/tofu"
REFRESH_INTERVAL="${REFRESH_INTERVAL:-5}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Expected resources for full provision (approximate)
EXPECTED_RESOURCES=(
    "aws_vpc.main"
    "aws_internet_gateway.main"
    "aws_eip.nat"
    "aws_nat_gateway.main"
    "aws_subnet.public"
    "aws_subnet.private"
    "aws_route_table.public"
    "aws_route_table.private"
    "aws_security_group.bastion"
    "aws_security_group.control_plane"
    "aws_security_group.worker"
    "aws_s3_bucket.cybersec"
    "aws_iam_role.rke2_node"
    "aws_instance.bastion"
    "aws_instance.control_plane"
    "aws_instance.worker"
)

get_phase() {
    # Detect current phase based on files and processes
    if pgrep -f "tofu plan" >/dev/null 2>&1; then
        echo "planning"
    elif pgrep -f "tofu apply" >/dev/null 2>&1; then
        echo "applying"
    elif pgrep -f "tofu destroy" >/dev/null 2>&1; then
        echo "destroying"
    elif pgrep -f "conftest" >/dev/null 2>&1; then
        echo "validating"
    elif [ -f "$TOFU_DIR/tfplan" ] && [ ! -f "$TOFU_DIR/tfplan.json" ]; then
        echo "planned"
    elif [ -f "$TOFU_DIR/destroy.tfplan" ] && [ ! -f "$TOFU_DIR/destroy.tfplan.json" ]; then
        echo "planned"
    elif [ -f "$TOFU_DIR/policy_input.json" ]; then
        echo "validated"
    else
        echo "idle"
    fi
}

get_state_resources() {
    cd "$TOFU_DIR" 2>/dev/null || return
    tofu state list 2>/dev/null | wc -l
}

get_plan_summary() {
    # Check for destroy plan first (aws:destroy), then regular plan (aws:provision)
    local plan_file=""
    if [ -f "$TOFU_DIR/destroy.tfplan.json" ]; then
        plan_file="$TOFU_DIR/destroy.tfplan.json"
    elif [ -f "$TOFU_DIR/tfplan.json" ]; then
        plan_file="$TOFU_DIR/tfplan.json"
    fi

    if [ -n "$plan_file" ]; then
        local add=$(jq '[.resource_changes[]? | select(.change.actions[] == "create")] | length' "$plan_file" 2>/dev/null || echo 0)
        local change=$(jq '[.resource_changes[]? | select(.change.actions[] == "update")] | length' "$plan_file" 2>/dev/null || echo 0)
        local destroy=$(jq '[.resource_changes[]? | select(.change.actions[] == "delete")] | length' "$plan_file" 2>/dev/null || echo 0)
        echo "+$add ~$change -$destroy"
    else
        echo "no plan"
    fi
}

get_policy_result() {
    if [ -f "$TOFU_DIR/policy_input.json" ]; then
        local region=$(jq -r '.context.aws_region // "unknown"' "$TOFU_DIR/policy_input.json" 2>/dev/null)
        local s3_region=$(jq -r '.context.s3_bucket_region // ""' "$TOFU_DIR/policy_input.json" 2>/dev/null)
        local ssh_key=$(jq -r '.context.ssh_key_exists // true' "$TOFU_DIR/policy_input.json" 2>/dev/null)

        local issues=""
        if [ -n "$s3_region" ] && [ "$s3_region" != "$region" ] && [ "$s3_region" != "null" ] && [ "$s3_region" != "" ]; then
            issues="${issues}S3 region mismatch ($s3_region vs $region); "
        fi
        if [ "$ssh_key" = "false" ]; then
            issues="${issues}SSH key missing; "
        fi

        if [ -n "$issues" ]; then
            echo -e "${RED}BLOCKED: $issues${NC}"
        else
            echo -e "${GREEN}Region: $region${NC}"
        fi
    else
        echo "pending"
    fi
}

get_recent_errors() {
    # Check for recent tofu errors in the state directory or common log locations
    local errors=""

    # Check if there's a recent apply that failed
    if [ -f "$TOFU_DIR/errored.tfstate" ]; then
        errors="tfstate error detected"
    fi

    # Check terraform.log if it exists
    if [ -f "$TOFU_DIR/terraform.log" ]; then
        local recent_error=$(tail -50 "$TOFU_DIR/terraform.log" 2>/dev/null | grep -i "error:" | tail -1)
        if [ -n "$recent_error" ]; then
            errors="$recent_error"
        fi
    fi

    echo "$errors"
}

check_ssh_key() {
    local region="${1:-us-east-1}"
    local key_name="${2:-}"

    if [ -z "$key_name" ]; then
        key_name=$(jq -r '.plan.variables.ssh_key_name.value // ""' "$TOFU_DIR/policy_input.json" 2>/dev/null)
    fi

    if [ -z "$key_name" ]; then
        echo -e "  ${YELLOW}?${NC} SSH Key (unknown)"
        return
    fi

    if aws ec2 describe-key-pairs --key-names "$key_name" --region "$region" >/dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} SSH Key ($key_name)"
    else
        echo -e "  ${RED}✗${NC} SSH Key ($key_name) - MISSING in $region"
    fi
}

check_aws_resources() {
    # Get region from policy_input.json if available, otherwise fallback
    local region="${AWS_REGION:-$(jq -r '.context.aws_region // "us-east-1"' "$TOFU_DIR/policy_input.json" 2>/dev/null || echo "us-east-1")}"
    local prefix="${1:-}"

    echo ""
    echo -e "${BLUE}AWS Resources (${region}):${NC}"

    # Check SSH key first - it's a prerequisite
    check_ssh_key "$region"

    # VPC
    local vpcs=$(aws ec2 describe-vpcs --filters "Name=tag:Project,Values=cybersec-dask" --region "$region" --query 'length(Vpcs)' --output text 2>/dev/null || echo 0)
    [ "$vpcs" -gt 0 ] && echo -e "  ${GREEN}✓${NC} VPC ($vpcs)" || echo -e "  ${YELLOW}○${NC} VPC"

    # Subnets
    local subnets=$(aws ec2 describe-subnets --filters "Name=tag:Project,Values=cybersec-dask" --region "$region" --query 'length(Subnets)' --output text 2>/dev/null || echo 0)
    [ "$subnets" -gt 0 ] && echo -e "  ${GREEN}✓${NC} Subnets ($subnets)" || echo -e "  ${YELLOW}○${NC} Subnets"

    # Security Groups
    local sgs=$(aws ec2 describe-security-groups --filters "Name=tag:Project,Values=cybersec-dask" --region "$region" --query 'length(SecurityGroups)' --output text 2>/dev/null || echo 0)
    [ "$sgs" -gt 0 ] && echo -e "  ${GREEN}✓${NC} Security Groups ($sgs)" || echo -e "  ${YELLOW}○${NC} Security Groups"

    # EC2 Instances (running/pending)
    local running=$(aws ec2 describe-instances --filters "Name=tag:Project,Values=cybersec-dask" "Name=instance-state-name,Values=running,pending" --region "$region" --query 'length(Reservations[].Instances[])' --output text 2>/dev/null || echo 0)
    # EC2 Instances (shutting-down/stopping - shown during destroy)
    local stopping=$(aws ec2 describe-instances --filters "Name=tag:Project,Values=cybersec-dask" "Name=instance-state-name,Values=shutting-down,stopping" --region "$region" --query 'length(Reservations[].Instances[])' --output text 2>/dev/null || echo 0)
    if [ "$running" -gt 0 ] && [ "$stopping" -gt 0 ]; then
        echo -e "  ${YELLOW}⟳${NC} EC2 Instances ($running running, $stopping terminating)"
    elif [ "$running" -gt 0 ]; then
        echo -e "  ${GREEN}✓${NC} EC2 Instances ($running)"
    elif [ "$stopping" -gt 0 ]; then
        echo -e "  ${RED}⟳${NC} EC2 Instances ($stopping terminating)"
    else
        echo -e "  ${YELLOW}○${NC} EC2 Instances"
    fi

    # S3 Bucket
    local bucket="cybersec-dask-${prefix}-data"
    if aws s3api head-bucket --bucket "$bucket" --region "$region" >/dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} S3 Bucket ($bucket)"
    else
        echo -e "  ${YELLOW}○${NC} S3 Bucket"
    fi

    # NAT Gateway
    local nats=$(aws ec2 describe-nat-gateways --filter "Name=tag:Project,Values=cybersec-dask" "Name=state,Values=available,pending" --region "$region" --query 'length(NatGateways)' --output text 2>/dev/null || echo 0)
    local nats_deleting=$(aws ec2 describe-nat-gateways --filter "Name=tag:Project,Values=cybersec-dask" "Name=state,Values=deleting" --region "$region" --query 'length(NatGateways)' --output text 2>/dev/null || echo 0)
    if [ "$nats" -gt 0 ] && [ "$nats_deleting" -gt 0 ]; then
        echo -e "  ${YELLOW}⟳${NC} NAT Gateway ($nats active, $nats_deleting deleting)"
    elif [ "$nats" -gt 0 ]; then
        echo -e "  ${GREEN}✓${NC} NAT Gateway ($nats)"
    elif [ "$nats_deleting" -gt 0 ]; then
        echo -e "  ${RED}⟳${NC} NAT Gateway ($nats_deleting deleting)"
    else
        echo -e "  ${YELLOW}○${NC} NAT Gateway"
    fi
}

show_status() {
    clear
    local phase=$(get_phase)
    local state_count=$(get_state_resources)
    local plan_summary=$(get_plan_summary)
    local policy_result=$(get_policy_result)

    # Get developer prefix
    local prefix=$(cd "$TOFU_DIR" && tofu output -raw s3_bucket_name 2>/dev/null | sed 's/cybersec-dask-\(.*\)-data/\1/' || echo "00631868")

    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║              AWS Infrastructure Monitor                    ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    # Phase indicator
    echo -n "Phase: "
    case "$phase" in
        planning)   echo -e "${YELLOW}⟳ Planning...${NC}" ;;
        validating) echo -e "${YELLOW}⟳ Validating policies...${NC}" ;;
        applying)   echo -e "${YELLOW}⟳ Applying changes...${NC}" ;;
        destroying) echo -e "${RED}⟳ Destroying...${NC}" ;;
        planned)    echo -e "${BLUE}◉ Plan ready${NC}" ;;
        validated)  echo -e "${BLUE}◉ Validated${NC}" ;;
        idle)       echo -e "${GREEN}◉ Idle${NC}" ;;
    esac

    echo "Plan: $plan_summary"
    echo "Policy: $policy_result"
    echo "State: $state_count resources"

    check_aws_resources "$prefix"

    # Check for errors
    local errors=$(get_recent_errors)
    if [ -n "$errors" ]; then
        echo ""
        echo -e "${RED}Errors:${NC}"
        echo -e "  ${RED}$errors${NC}"
    fi

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
