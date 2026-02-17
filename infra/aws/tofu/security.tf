# -----------------------------------------------------------------------------
# Security Groups for RKE2 Cluster
# -----------------------------------------------------------------------------

# Bastion host security group
resource "aws_security_group" "bastion" {
  name        = "${var.project}-bastion"
  description = "Security group for bastion host"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH from allowed CIDRs"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project}-bastion-sg"
  })
}

# RKE2 control plane security group
resource "aws_security_group" "control_plane" {
  name        = "${var.project}-control-plane"
  description = "Security group for RKE2 control plane nodes"
  vpc_id      = aws_vpc.main.id

  # SSH from bastion
  ingress {
    description     = "SSH from bastion"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [aws_security_group.bastion.id]
  }

  # Kubernetes API server
  ingress {
    description = "Kubernetes API"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # RKE2 supervisor API (for agent registration)
  ingress {
    description = "RKE2 supervisor API"
    from_port   = 9345
    to_port     = 9345
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # etcd client and peer
  ingress {
    description = "etcd client"
    from_port   = 2379
    to_port     = 2380
    protocol    = "tcp"
    self        = true
  }

  # Kubelet API
  ingress {
    description = "Kubelet API"
    from_port   = 10250
    to_port     = 10250
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Flannel VXLAN (CNI)
  ingress {
    description = "Flannel VXLAN"
    from_port   = 8472
    to_port     = 8472
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Canal/Calico BGP (if using Calico)
  ingress {
    description = "Canal BGP"
    from_port   = 179
    to_port     = 179
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # NodePort services range
  ingress {
    description = "NodePort services"
    from_port   = 30000
    to_port     = 32767
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Full egress when not in air-gap mode
  dynamic "egress" {
    for_each = var.airgap_mode ? [] : [1]
    content {
      from_port   = 0
      to_port     = 0
      protocol    = "-1"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  # Air-gap: VPC-only egress (all protocols)
  dynamic "egress" {
    for_each = var.airgap_mode ? [1] : []
    content {
      description = "VPC internal traffic"
      from_port   = 0
      to_port     = 0
      protocol    = "-1"
      cidr_blocks = [var.vpc_cidr]
    }
  }

  # Air-gap: S3 via VPC gateway endpoint (prefix list)
  dynamic "egress" {
    for_each = var.airgap_mode ? [1] : []
    content {
      description     = "S3 via VPC endpoint"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      prefix_list_ids = [aws_vpc_endpoint.s3.prefix_list_id]
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.project}-control-plane-sg"
  })
}

# RKE2 worker security group
resource "aws_security_group" "worker" {
  name        = "${var.project}-worker"
  description = "Security group for RKE2 worker nodes"
  vpc_id      = aws_vpc.main.id

  # SSH from bastion
  ingress {
    description     = "SSH from bastion"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [aws_security_group.bastion.id]
  }

  # Kubelet API
  ingress {
    description = "Kubelet API"
    from_port   = 10250
    to_port     = 10250
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Flannel VXLAN
  ingress {
    description = "Flannel VXLAN"
    from_port   = 8472
    to_port     = 8472
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Canal/Calico BGP
  ingress {
    description = "Canal BGP"
    from_port   = 179
    to_port     = 179
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # NodePort services range
  ingress {
    description = "NodePort services"
    from_port   = 30000
    to_port     = 32767
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Dask scheduler dashboard (via NodePort)
  ingress {
    description = "Dask dashboard"
    from_port   = 8787
    to_port     = 8788
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Full egress when not in air-gap mode
  dynamic "egress" {
    for_each = var.airgap_mode ? [] : [1]
    content {
      from_port   = 0
      to_port     = 0
      protocol    = "-1"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  # Air-gap: VPC-only egress (all protocols)
  dynamic "egress" {
    for_each = var.airgap_mode ? [1] : []
    content {
      description = "VPC internal traffic"
      from_port   = 0
      to_port     = 0
      protocol    = "-1"
      cidr_blocks = [var.vpc_cidr]
    }
  }

  # Air-gap: S3 via VPC gateway endpoint (prefix list)
  dynamic "egress" {
    for_each = var.airgap_mode ? [1] : []
    content {
      description     = "S3 via VPC endpoint"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      prefix_list_ids = [aws_vpc_endpoint.s3.prefix_list_id]
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.project}-worker-sg"
  })
}
