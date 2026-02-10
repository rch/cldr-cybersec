# AWS quota validation for Terraform/OpenTofu plans
#
# This policy checks if the deployment will exceed AWS quotas.
# Quotas are passed via context.quotas from the pre-flight check.
#
# Example context:
#   {
#     "quotas": {
#       "eip_available": 4,
#       "eip_limit": 5,
#       "eip_current": 1,
#       "vpc_available": 3,
#       "vpc_limit": 5,
#       "vpc_current": 2
#     }
#   }

package tofu.quota

import rego.v1

import data.tofu.base

# Get EIP resources being created
eips_to_create := count([r |
	some r in base.plan.resource_changes
	r.type == "aws_eip"
	some action in r.change.actions
	action == "create"
])

# Get VPC resources being created
vpcs_to_create := count([r |
	some r in base.plan.resource_changes
	r.type == "aws_vpc"
	some action in r.change.actions
	action == "create"
])

# Deny if creating more EIPs than available quota
deny contains msg if {
	quota := object.get(base.context, "quotas", {})
	eip_available := object.get(quota, "eip_available", -1)
	eip_available >= 0  # Only check if quota info is available
	eips_to_create > eip_available
	eip_current := object.get(quota, "eip_current", 0)
	eip_limit := object.get(quota, "eip_limit", 5)
	msg := sprintf(
		"EIP quota exceeded: plan creates %d EIP(s) but only %d available (%d/%d in use). Run: /aws preflight --eips to see current allocations",
		[eips_to_create, eip_available, eip_current, eip_limit],
	)
}

# Deny if creating more VPCs than available quota
deny contains msg if {
	quota := object.get(base.context, "quotas", {})
	vpc_available := object.get(quota, "vpc_available", -1)
	vpc_available >= 0  # Only check if quota info is available
	vpcs_to_create > vpc_available
	vpc_current := object.get(quota, "vpc_current", 0)
	vpc_limit := object.get(quota, "vpc_limit", 5)
	msg := sprintf(
		"VPC quota exceeded: plan creates %d VPC(s) but only %d available (%d/%d in use)",
		[vpcs_to_create, vpc_available, vpc_current, vpc_limit],
	)
}

# Warn if EIP quota will be tight after deployment
warn contains msg if {
	quota := object.get(base.context, "quotas", {})
	eip_available := object.get(quota, "eip_available", -1)
	eip_available >= 0
	eips_to_create <= eip_available
	eips_to_create > 0
	remaining := eip_available - eips_to_create
	remaining < 2  # Less than 2 EIPs remaining after deployment
	msg := sprintf("Low EIP quota: only %d EIP(s) will remain after deployment", [remaining])
}

# Info about EIP allocation in plan
info contains msg if {
	eips_to_create > 0
	msg := sprintf("Plan allocates %d Elastic IP(s)", [eips_to_create])
}

# Info about VPC creation in plan
info contains msg if {
	vpcs_to_create > 0
	msg := sprintf("Plan creates %d VPC(s)", [vpcs_to_create])
}
