# Base policy helpers for OpenTofu/Terraform plan validation
#
# This package provides common accessors and utility functions
# for evaluating Terraform plans with developer isolation.

package tofu.base

import rego.v1

# Access input sections
plan := input.plan

context := input.context

# Helper: get resources being deleted
resources_to_delete := [r |
	some r in plan.resource_changes
	some action in r.change.actions
	action == "delete"
]

# Helper: count resources by type
count_by_type(type) := count([r |
	some r in resources_to_delete
	r.type == type
])

# Helper: get resource types being deleted
resource_types_deleted := {r.type |
	some r in resources_to_delete
}

# Helper: check if resource belongs to developer
resource_belongs_to_developer(rc) if {
	# Check Owner tag matches developer email
	owner := rc.change.before.tags.Owner
	owner == context.developer_email
}

resource_belongs_to_developer(rc) if {
	# No Owner tag - assume developer owns it
	not rc.change.before.tags.Owner
}

resource_belongs_to_developer(rc) if {
	# Empty Owner tag - assume developer owns it
	rc.change.before.tags.Owner == ""
}

# Helper: check if resource name contains developer prefix
resource_has_developer_prefix(rc) if {
	contains(rc.address, context.developer_prefix)
}

resource_has_developer_prefix(rc) if {
	# Resource doesn't have cybersec- prefix pattern - skip check
	not contains(rc.address, "cybersec-")
}

# =============================================================================
# Region Consistency Helpers
# =============================================================================

# Get planned region from variables (handles missing gracefully)
planned_region := plan.variables.aws_region.value if {
	plan.variables.aws_region.value
} else := ""

# Get availability zones from variables (handles missing gracefully)
planned_azs := plan.variables.availability_zones.value if {
	plan.variables.availability_zones.value
} else := []

# Check if availability zones match the planned region
azs_match_region if {
	count(planned_azs) == 0
}

azs_match_region if {
	planned_region == ""
}

azs_match_region if {
	count(planned_azs) > 0
	planned_region != ""
	every az in planned_azs {
		startswith(az, planned_region)
	}
}

# Get mismatched AZs for error messages
mismatched_azs := [az |
	some az in planned_azs
	not startswith(az, planned_region)
]

# Check if S3 bucket region matches planned region
s3_region_matches if {
	context.s3_bucket_region == ""
}

s3_region_matches if {
	context.s3_bucket_region == planned_region
}
