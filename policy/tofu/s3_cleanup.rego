# S3 cleanup guardrails
#
# Validates S3 bucket cleanup operations to ensure:
# - Bucket belongs to the current developer (prefix and Owner tag)
# - Provides warnings about data deletion

package tofu.s3_cleanup

import rego.v1

context := input.context

# Deny if bucket doesn't contain developer prefix
deny contains msg if {
	not contains(context.s3_bucket, context.developer_prefix)
	msg := sprintf("Bucket '%s' does not match your prefix (%s)", [context.s3_bucket, context.developer_prefix])
}

# Deny if bucket owner tag doesn't match (when owner is set)
deny contains msg if {
	context.bucket_owner != ""
	context.bucket_owner != context.developer_email
	msg := sprintf("Cannot clear bucket owned by %s (you are %s)", [context.bucket_owner, context.developer_email])
}

# Warn about large deletions (> 1000 objects)
warn contains msg if {
	context.s3_object_count > 1000
	msg := sprintf("Bucket contains %d objects (large deletion)", [context.s3_object_count])
}

# Warn about moderate deletions (> 100 objects)
warn contains msg if {
	context.s3_object_count > 100
	context.s3_object_count <= 1000
	msg := sprintf("Bucket contains %d objects", [context.s3_object_count])
}

# Info about bucket being cleared
info contains msg if {
	msg := sprintf("Clearing bucket: %s (%d objects)", [context.s3_bucket, context.s3_object_count])
}

# Info about developer identity
info contains msg if {
	msg := sprintf("Developer: %s (prefix: %s)", [context.developer_email, context.developer_prefix])
}
