# -----------------------------------------------------------------------------
# Outputs for Ansible Integration
# -----------------------------------------------------------------------------

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "bastion_public_ip" {
  description = "Bastion host public IP"
  value       = aws_instance.bastion.public_ip
}

output "bastion_fqdn" {
  description = "Bastion FQDN (routed through WARP split tunnel)"
  value       = local.ingress_domains.bastion
}

output "bastion_instance_id" {
  description = "Bastion instance ID (for SSM)"
  value       = aws_instance.bastion.id
}

output "control_plane_private_ips" {
  description = "Dask control plane node private IPs"
  value       = aws_instance.control_plane[*].private_ip
}

output "control_plane_instance_ids" {
  description = "Dask control plane instance IDs"
  value       = aws_instance.control_plane[*].id
}

output "dask_worker_private_ips" {
  description = "Dask worker node private IPs"
  value       = aws_instance.worker[*].private_ip
}

output "dask_worker_instance_ids" {
  description = "Dask worker instance IDs"
  value       = aws_instance.worker[*].id
}

# --- Flink cluster outputs ---

output "flink_control_plane_private_ips" {
  description = "Flink control plane node private IPs"
  value       = var.enable_flink_cluster ? aws_instance.flink_control_plane[*].private_ip : []
}

output "flink_worker_private_ips" {
  description = "Flink worker node private IPs"
  value       = var.enable_flink_cluster ? aws_instance.flink_worker[*].private_ip : []
}

output "flink_k8s_api_endpoint" {
  description = "Flink Kubernetes API endpoint (internal NLB)"
  value       = var.enable_flink_cluster ? "https://${aws_lb.flink_k8s_api[0].dns_name}:6443" : ""
}

output "k8s_api_endpoint" {
  description = "Kubernetes API endpoint (internal NLB)"
  value       = "https://${aws_lb.k8s_api.dns_name}:6443"
}

output "rke2_server_url" {
  description = "RKE2 server URL for agent registration"
  value       = "https://${aws_lb.k8s_api.dns_name}:9345"
}

output "s3_bucket_name" {
  description = "S3 bucket name for data storage"
  value       = aws_s3_bucket.cybersec.id
}

output "s3_bucket_arn" {
  description = "S3 bucket ARN"
  value       = aws_s3_bucket.cybersec.arn
}

# -----------------------------------------------------------------------------
# Security Log Pipeline Outputs
# -----------------------------------------------------------------------------

output "security_logs_bucket" {
  description = "S3 bucket for raw CloudTrail and VPC Flow Logs"
  value       = var.enable_security_logs ? aws_s3_bucket.security_logs[0].id : ""
}

output "security_logs_bucket_arn" {
  description = "S3 bucket ARN for security logs"
  value       = var.enable_security_logs ? aws_s3_bucket.security_logs[0].arn : ""
}

output "cloudtrail_arn" {
  description = "CloudTrail trail ARN"
  value       = var.enable_security_logs ? aws_cloudtrail.trail[0].arn : ""
}

output "flink_source_paths" {
  description = "S3 paths for Flink FileSource monitoring"
  value = var.enable_security_logs ? {
    cloudtrail   = "s3://${aws_s3_bucket.security_logs[0].id}/AWSLogs/${data.aws_caller_identity.current.account_id}/CloudTrail/${var.aws_region}/"
    vpc_flowlogs = "s3://${aws_s3_bucket.security_logs[0].id}/AWSLogs/${data.aws_caller_identity.current.account_id}/vpcflowlogs/${var.aws_region}/"
  } : {}
}

# Generate Ansible inventory
output "ansible_inventory" {
  description = "Ansible inventory in INI format"
  value = templatefile("${path.module}/templates/inventory.tftpl", {
    bastion_ip              = aws_instance.bastion.public_ip
    control_plane_ips       = aws_instance.control_plane[*].private_ip
    worker_ips              = aws_instance.worker[*].private_ip
    ssh_key_name            = var.ssh_key_name
    k8s_api_endpoint        = aws_lb.k8s_api.dns_name
    s3_bucket_name          = aws_s3_bucket.cybersec.id
    aws_region              = var.aws_region
    enable_flink_cluster    = var.enable_flink_cluster
    flink_control_plane_ips = var.enable_flink_cluster ? aws_instance.flink_control_plane[*].private_ip : []
    flink_worker_ips        = var.enable_flink_cluster ? aws_instance.flink_worker[*].private_ip : []
    flink_k8s_api_endpoint  = var.enable_flink_cluster ? aws_lb.flink_k8s_api[0].dns_name : ""
    security_logs_bucket    = var.enable_security_logs ? aws_s3_bucket.security_logs[0].id : ""
  })
}

# JSON output for programmatic consumption
output "cluster_info" {
  description = "Cluster information in JSON format"
  value = {
    vpc_id = aws_vpc.main.id
    region = var.aws_region
    bastion = {
      public_ip   = aws_instance.bastion.public_ip
      instance_id = aws_instance.bastion.id
    }
    control_planes = [
      for i, instance in aws_instance.control_plane : {
        private_ip  = instance.private_ip
        instance_id = instance.id
        az          = instance.availability_zone
      }
    ]
    dask_workers = [
      for i, instance in aws_instance.worker : {
        private_ip  = instance.private_ip
        instance_id = instance.id
        az          = instance.availability_zone
      }
    ]
    endpoints = {
      dask_k8s_api     = "https://${aws_lb.k8s_api.dns_name}:6443"
      dask_rke2_server = "https://${aws_lb.k8s_api.dns_name}:9345"
    }
    flink_cluster = var.enable_flink_cluster ? {
      enabled = true
      control_planes = [
        for i, instance in aws_instance.flink_control_plane : {
          private_ip  = instance.private_ip
          instance_id = instance.id
          az          = instance.availability_zone
        }
      ]
      workers = [
        for i, instance in aws_instance.flink_worker : {
          private_ip  = instance.private_ip
          instance_id = instance.id
          az          = instance.availability_zone
        }
      ]
      endpoints = {
        k8s_api     = "https://${aws_lb.flink_k8s_api[0].dns_name}:6443"
        rke2_server = "https://${aws_lb.flink_k8s_api[0].dns_name}:9345"
      }
      } : {
      enabled        = false
      control_planes = []
      workers        = []
      endpoints      = {}
    }
    s3 = {
      bucket_name = aws_s3_bucket.cybersec.id
      bucket_arn  = aws_s3_bucket.cybersec.arn
      region      = var.aws_region
    }
    developer = {
      prefix = var.developer_prefix
      email  = var.developer_email
    }
    airgap = {
      enabled = var.airgap_mode
      nodeports = var.airgap_mode ? {
        dask_dashboard = 30087
        dask_scheduler = 30086
        jupyterhub     = 30080
        panel_viz      = 30506
      } : {}
      routing = var.airgap_mode ? "bastion → control_plane NodePorts" : "K8s service DNS"
    }
    data_pipeline = {
      security_logs_enabled = var.enable_security_logs
      security_logs_bucket  = var.enable_security_logs ? aws_s3_bucket.security_logs[0].id : ""
      flink_source_paths = var.enable_security_logs ? {
        cloudtrail   = "s3://${aws_s3_bucket.security_logs[0].id}/AWSLogs/${data.aws_caller_identity.current.account_id}/CloudTrail/${var.aws_region}/"
        vpc_flowlogs = "s3://${aws_s3_bucket.security_logs[0].id}/AWSLogs/${data.aws_caller_identity.current.account_id}/vpcflowlogs/${var.aws_region}/"
      } : {}
      iceberg_warehouse = "s3://${aws_s3_bucket.cybersec.id}/iceberg/warehouse/"
      otel_data_path    = "s3://${aws_s3_bucket.cybersec.id}/otel/"
    }
  }
}
