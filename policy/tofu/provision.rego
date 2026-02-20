# Provision operation guardrails
#
# Validates Terraform apply plans to ensure:
# - Resources being created have correct Owner tags
# - Resource names contain developer prefix
# - Provides warnings about what will be created

package tofu.provision

import rego.v1

import data.tofu.base

# Helper: get resources being created
resources_to_create := [r |
	some r in base.plan.resource_changes
	some action in r.change.actions
	action == "create"
]

# Helper: get resources being updated
resources_to_update := [r |
	some r in base.plan.resource_changes
	some action in r.change.actions
	action == "update"
]

# Helper: count resources being created by type
count_create_by_type(type) := count([r |
	some r in resources_to_create
	r.type == type
])

# Helper: get resource types being created
resource_types_created := {r.type |
	some r in resources_to_create
}

# Deny if creating resources with Owner tag that doesn't match developer
deny contains msg if {
	some rc in resources_to_create
	owner := rc.change.after.tags.Owner
	owner != ""
	owner != base.context.developer_email
	msg := sprintf("Resource '%s' has Owner tag '%s' but you are '%s'", [rc.address, owner, base.context.developer_email])
}

# Deny if creating cybersec resources without developer prefix in name
deny contains msg if {
	some rc in resources_to_create
	contains(rc.address, "cybersec-")
	not contains(rc.address, base.context.developer_prefix)
	# Also check the resource name if available
	name := object.get(rc.change.after, "name", "")
	name != ""
	contains(name, "cybersec-")
	not contains(name, base.context.developer_prefix)
	msg := sprintf("Resource '%s' name doesn't contain your prefix (%s)", [rc.address, base.context.developer_prefix])
}

# Warn about EC2 instances being created
warn contains msg if {
	ec2_count := count_create_by_type("aws_instance")
	ec2_count > 0
	msg := sprintf("Creating %d EC2 instance(s)", [ec2_count])
}

# Warn about Elastic IPs being allocated
warn contains msg if {
	eip_count := count_create_by_type("aws_eip")
	eip_count > 0
	msg := sprintf("Allocating %d Elastic IP(s)", [eip_count])
}

# Warn about VPCs being created
warn contains msg if {
	vpc_count := count_create_by_type("aws_vpc")
	vpc_count > 0
	msg := sprintf("Creating %d VPC(s)", [vpc_count])
}

# Warn about IAM roles being created
warn contains msg if {
	iam_count := count_create_by_type("aws_iam_role")
	iam_count > 0
	msg := sprintf("Creating %d IAM role(s)", [iam_count])
}

# Warn about S3 buckets being created
warn contains msg if {
	s3_count := count_create_by_type("aws_s3_bucket")
	s3_count > 0
	msg := sprintf("Creating %d S3 bucket(s)", [s3_count])
}

# Warn about security groups
warn contains msg if {
	sg_count := count_create_by_type("aws_security_group")
	sg_count > 0
	msg := sprintf("Creating %d security group(s)", [sg_count])
}

# Info summary of resources to create
info contains msg if {
	total := count(resources_to_create)
	total > 0
	msg := sprintf("Plan will create %d resource(s)", [total])
}

# Info summary of resources to update
info contains msg if {
	total := count(resources_to_update)
	total > 0
	msg := sprintf("Plan will update %d resource(s)", [total])
}

# Info about resource types
info contains msg if {
	types := resource_types_created
	count(types) > 0
	msg := sprintf("Resource types: %v", [types])
}

# Info about developer identity
info contains msg if {
	msg := sprintf("Developer: %s (prefix: %s)", [base.context.developer_email, base.context.developer_prefix])
}

# Info about AWS account
info contains msg if {
	base.context.aws_account_id != ""
	base.context.aws_account_id != "unknown"
	msg := sprintf("AWS Account: %s", [base.context.aws_account_id])
}

# =============================================================================
# Region Consistency Checks
# =============================================================================

# Deny if availability zones don't match the planned region
deny contains msg if {
	not base.azs_match_region
	some az in base.mismatched_azs
	msg := sprintf("Region mismatch: availability zone '%s' does not match region '%s' - operation will hang or fail", [az, base.planned_region])
}

# Deny if context shows S3 bucket in different region than planned
deny contains msg if {
	not base.s3_region_matches
	base.context.s3_bucket_region != ""
	msg := sprintf("S3 bucket is in region '%s' but plan targets '%s' - operation will fail with 'MovedPermanently' error", [base.context.s3_bucket_region, base.planned_region])
}

# Info about region configuration
info contains msg if {
	base.planned_region != ""
	msg := sprintf("Target region: %s", [base.planned_region])
}

# =============================================================================
# Key Pair Validation
# =============================================================================

# Deny if key pair doesn't exist in target region
deny contains msg if {
	base.context.ssh_key_exists == false
	key_name := base.plan.variables.ssh_key_name.value
	msg := sprintf("SSH key pair '%s' does not exist in region '%s' - run: AWS_REGION=%s devenv tasks run aws:keypair:ensure", [key_name, base.planned_region, base.planned_region])
}
