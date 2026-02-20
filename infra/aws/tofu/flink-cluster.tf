# -----------------------------------------------------------------------------
# Flink/NiFi RKE2 Cluster — Separate from Dask/JupyterHub
#
# Independent RKE2 cluster for streaming workloads. Shares VPC, bastion,
# S3 buckets, and Cloudflare tunnel with the Dask cluster but has its own
# control plane, workers, NLB, and security groups.
#
# Gated on var.enable_flink_cluster (default: false).
#
# Instance sizing rationale:
#   Dask workers  → r6i.xlarge (4 vCPU, 32 GiB) — memory for DataFrame ops
#   Flink workers → c6i.xlarge (4 vCPU, 8 GiB)  — CPU for stream processing
#     + 200 GB data volume for RocksDB state backend and checkpoints
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Security Groups
# -----------------------------------------------------------------------------

resource "aws_security_group" "flink_control_plane" {
  count       = var.enable_flink_cluster ? 1 : 0
  name        = "${var.project}-flink-control-plane"
  description = "Security group for Flink RKE2 control plane"
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

  # Air-gap: VPC-only egress
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

  # Air-gap: S3 via VPC gateway endpoint
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
    Name    = "${var.project}-flink-control-plane-sg"
    Cluster = "flink"
  })
}

resource "aws_security_group" "flink_worker" {
  count       = var.enable_flink_cluster ? 1 : 0
  name        = "${var.project}-flink-worker"
  description = "Security group for Flink RKE2 worker nodes"
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

  # Flink JobManager Web UI (via NodePort or direct)
  ingress {
    description = "Flink Web UI"
    from_port   = 8081
    to_port     = 8081
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # NiFi Web UI
  ingress {
    description = "NiFi Web UI"
    from_port   = 8450
    to_port     = 8450
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

  # Air-gap: VPC-only egress
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

  # Air-gap: S3 via VPC gateway endpoint
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
    Name    = "${var.project}-flink-worker-sg"
    Cluster = "flink"
  })
}

# -----------------------------------------------------------------------------
# Flink Control Plane
# -----------------------------------------------------------------------------

resource "aws_instance" "flink_control_plane" {
  count                  = var.enable_flink_cluster ? var.flink_control_plane_count : 0
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.flink_control_plane_instance_type
  key_name               = var.ssh_key_name
  subnet_id              = aws_subnet.private[count.index % length(aws_subnet.private)].id
  vpc_security_group_ids = [aws_security_group.flink_control_plane[0].id]
  iam_instance_profile   = aws_iam_instance_profile.rke2_node.name

  root_block_device {
    volume_size = var.flink_root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, {
    Name                                   = "${var.project}-flink-control-plane-${count.index + 1}"
    Role                                   = "control-plane"
    Cluster                                = "flink"
    "kubernetes.io/cluster/${var.project}" = "owned"
  })
}

# -----------------------------------------------------------------------------
# Flink Worker Nodes
# Compute-optimized (c6i) for stream processing, with data volume for
# RocksDB state backend and Flink checkpoints.
# -----------------------------------------------------------------------------

resource "aws_instance" "flink_worker" {
  count                  = var.enable_flink_cluster ? var.flink_worker_count : 0
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.flink_worker_instance_type
  key_name               = var.ssh_key_name
  subnet_id              = aws_subnet.private[count.index % length(aws_subnet.private)].id
  vpc_security_group_ids = [aws_security_group.flink_worker[0].id]
  iam_instance_profile   = aws_iam_instance_profile.rke2_node.name

  root_block_device {
    volume_size = var.flink_root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  # Data volume for RocksDB state + checkpoints
  ebs_block_device {
    device_name = "/dev/sdf"
    volume_size = var.flink_data_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, {
    Name                                   = "${var.project}-flink-worker-${count.index + 1}"
    Role                                   = "worker"
    Cluster                                = "flink"
    "kubernetes.io/cluster/${var.project}" = "owned"
  })
}

# -----------------------------------------------------------------------------
# Network Load Balancer for Flink K8s API
# Separate NLB from the Dask cluster — each cluster has its own K8s API.
# -----------------------------------------------------------------------------

resource "aws_lb" "flink_k8s_api" {
  count              = var.enable_flink_cluster ? 1 : 0
  name               = "${var.project}-flink-k8s-api"
  internal           = true
  load_balancer_type = "network"
  subnets            = aws_subnet.private[*].id

  tags = merge(local.common_tags, {
    Name    = "${var.project}-flink-k8s-api-nlb"
    Cluster = "flink"
  })
}

resource "aws_lb_target_group" "flink_k8s_api" {
  count    = var.enable_flink_cluster ? 1 : 0
  name     = "${var.project}-flink-k8s-api"
  port     = 6443
  protocol = "TCP"
  vpc_id   = aws_vpc.main.id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
    port                = 6443
    protocol            = "TCP"
  }

  tags = merge(local.common_tags, {
    Cluster = "flink"
  })
}

resource "aws_lb_listener" "flink_k8s_api" {
  count             = var.enable_flink_cluster ? 1 : 0
  load_balancer_arn = aws_lb.flink_k8s_api[0].arn
  port              = 6443
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.flink_k8s_api[0].arn
  }
}

resource "aws_lb_target_group_attachment" "flink_k8s_api" {
  count            = var.enable_flink_cluster ? var.flink_control_plane_count : 0
  target_group_arn = aws_lb_target_group.flink_k8s_api[0].arn
  target_id        = aws_instance.flink_control_plane[count.index].id
  port             = 6443
}

# RKE2 supervisor API target group (for Flink agent registration)
resource "aws_lb_target_group" "flink_rke2_supervisor" {
  count    = var.enable_flink_cluster ? 1 : 0
  name     = "${var.project}-flink-rke2-sup"
  port     = 9345
  protocol = "TCP"
  vpc_id   = aws_vpc.main.id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
    port                = 9345
    protocol            = "TCP"
  }

  tags = merge(local.common_tags, {
    Cluster = "flink"
  })
}

resource "aws_lb_listener" "flink_rke2_supervisor" {
  count             = var.enable_flink_cluster ? 1 : 0
  load_balancer_arn = aws_lb.flink_k8s_api[0].arn
  port              = 9345
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.flink_rke2_supervisor[0].arn
  }
}

resource "aws_lb_target_group_attachment" "flink_rke2_supervisor" {
  count            = var.enable_flink_cluster ? var.flink_control_plane_count : 0
  target_group_arn = aws_lb_target_group.flink_rke2_supervisor[0].arn
  target_id        = aws_instance.flink_control_plane[count.index].id
  port             = 9345
}
