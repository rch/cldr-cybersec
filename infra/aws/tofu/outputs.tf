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

output "bastion_instance_id" {
  description = "Bastion instance ID (for SSM)"
  value       = aws_instance.bastion.id
}

output "control_plane_private_ips" {
  description = "Control plane node private IPs"
  value       = aws_instance.control_plane[*].private_ip
}

output "control_plane_instance_ids" {
  description = "Control plane instance IDs"
  value       = aws_instance.control_plane[*].id
}

output "worker_private_ips" {
  description = "Worker node private IPs"
  value       = aws_instance.worker[*].private_ip
}

output "worker_instance_ids" {
  description = "Worker instance IDs"
  value       = aws_instance.worker[*].id
}

output "k8s_api_endpoint" {
  description = "Kubernetes API endpoint (internal NLB)"
  value       = "https://${aws_lb.k8s_api.dns_name}:6443"
}

output "rke2_server_url" {
  description = "RKE2 server URL for agent registration"
  value       = "https://${aws_lb.k8s_api.dns_name}:9345"
}

# Generate Ansible inventory
output "ansible_inventory" {
  description = "Ansible inventory in INI format"
  value = templatefile("${path.module}/templates/inventory.tftpl", {
    bastion_ip        = aws_instance.bastion.public_ip
    control_plane_ips = aws_instance.control_plane[*].private_ip
    worker_ips        = aws_instance.worker[*].private_ip
    ssh_key_name      = var.ssh_key_name
    k8s_api_endpoint  = aws_lb.k8s_api.dns_name
  })
}

# JSON output for programmatic consumption
output "cluster_info" {
  description = "Cluster information in JSON format"
  value = {
    vpc_id             = aws_vpc.main.id
    region             = var.aws_region
    bastion            = {
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
    workers = [
      for i, instance in aws_instance.worker : {
        private_ip  = instance.private_ip
        instance_id = instance.id
        az          = instance.availability_zone
      }
    ]
    endpoints = {
      k8s_api        = "https://${aws_lb.k8s_api.dns_name}:6443"
      rke2_server    = "https://${aws_lb.k8s_api.dns_name}:9345"
    }
  }
}
