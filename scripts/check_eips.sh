#!/bin/bash
set -e

REGIONS=(us-east-1 us-east-2 us-west-1 us-west-2 eu-west-1 eu-central-1 ap-southeast-1)

echo "Checking EIP usage..."
echo "---------------------"

for region in "${REGIONS[@]}"; do
    count=$(aws ec2 describe-addresses --region "$region" --query 'length(Addresses)' --output text 2>/dev/null || echo "Error")
    echo "$region: $count"
done
