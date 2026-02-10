# Teardown operation guardrails
#
# Combines destroy and S3 cleanup validation for complete teardown.
# This is the most destructive operation - validates both infrastructure
# and data deletion.

package tofu.teardown

import rego.v1

import data.tofu.base
import data.tofu.destroy
import data.tofu.s3_cleanup

# Import deny rules from destroy policy
deny contains msg if {
	some msg in destroy.deny
}

# Import deny rules from s3_cleanup policy (when S3 context present)
deny contains msg if {
	base.context.s3_bucket
	some msg in s3_cleanup.deny
}

# Import warnings from destroy policy
warn contains msg if {
	some msg in destroy.warn
}

# Import warnings from s3_cleanup policy (when S3 context present)
warn contains msg if {
	base.context.s3_bucket
	some msg in s3_cleanup.warn
}

# Teardown-specific warning
warn contains msg if {
	msg := "COMPLETE TEARDOWN: Both infrastructure and data will be destroyed"
}

# Import info from destroy policy
info contains msg if {
	some msg in destroy.info
}

# Import info from s3_cleanup policy (when S3 context present)
info contains msg if {
	base.context.s3_bucket
	some msg in s3_cleanup.info
}

# Teardown-specific info
info contains msg if {
	msg := sprintf("Operation: %s", [base.context.operation])
}
