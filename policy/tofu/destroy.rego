# Destroy operation guardrails
#
# Validates Terraform destroy plans to ensure:
# - Resources belong to the current developer (Owner tag check)
# - Resource names match developer prefix
# - Provides warnings about what will be destroyed

package tofu.destroy

import rego.v1

import data.tofu.base

# Deny if destroying resources owned by different developer
deny contains msg if {
	some rc in base.resources_to_delete
	owner := rc.change.before.tags.Owner
	owner != ""
	owner != base.context.developer_email
	msg := sprintf("Cannot destroy '%s' owned by %s (you are %s)", [rc.address, owner, base.context.developer_email])
}

# Deny if resource name contains cybersec-dask pattern but doesn't match developer prefix
deny contains msg if {
	some rc in base.resources_to_delete
	contains(rc.address, "cybersec-dask-")
	not contains(rc.address, base.context.developer_prefix)
	msg := sprintf("Resource '%s' does not match your prefix (%s)", [rc.address, base.context.developer_prefix])
}

# Warn about EC2 instances being destroyed
warn contains msg if {
	ec2_count := base.count_by_type("aws_instance")
	ec2_count > 0
	msg := sprintf("Destroying %d EC2 instance(s)", [ec2_count])
}

# Warn about Elastic IPs being released
warn contains msg if {
	eip_count := base.count_by_type("aws_eip")
	eip_count > 0
	msg := sprintf("Releasing %d Elastic IP(s)", [eip_count])
}

# Warn about VPCs being destroyed
warn contains msg if {
	vpc_count := base.count_by_type("aws_vpc")
	vpc_count > 0
	msg := sprintf("Destroying %d VPC(s)", [vpc_count])
}

# Warn about IAM roles being destroyed
warn contains msg if {
	iam_count := base.count_by_type("aws_iam_role")
	iam_count > 0
	msg := sprintf("Destroying %d IAM role(s)", [iam_count])
}

# Warn about S3 buckets being destroyed
warn contains msg if {
	s3_count := base.count_by_type("aws_s3_bucket")
	s3_count > 0
	msg := sprintf("Destroying %d S3 bucket(s)", [s3_count])
}

# Info summary of total resources
info contains msg if {
	total := count(base.resources_to_delete)
	total > 0
	msg := sprintf("Plan will destroy %d resource(s)", [total])
}

# Info about resource types
info contains msg if {
	types := base.resource_types_deleted
	count(types) > 0
	msg := sprintf("Resource types: %v", [types])
}

# =============================================================================
# Region Consistency Checks
# =============================================================================

# Deny if availability zones don't match the planned region
deny contains msg if {
	not base.azs_match_region
	some az in base.mismatched_azs
	msg := sprintf("Region mismatch: availability zone '%s' does not match region '%s' - destroy will hang or fail", [az, base.planned_region])
}

# Deny if S3 bucket is in different region (causes MovedPermanently errors)
deny contains msg if {
	not base.s3_region_matches
	base.context.s3_bucket_region != ""
	msg := sprintf("S3 bucket is in region '%s' but targeting '%s' - use AWS_REGION=%s for teardown", [base.context.s3_bucket_region, base.planned_region, base.context.s3_bucket_region])
}
