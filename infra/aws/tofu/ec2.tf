# -----------------------------------------------------------------------------
# EC2 Instances for RKE2 Cluster
# -----------------------------------------------------------------------------

# Get latest Amazon Linux 2023 AMI
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# IAM role for EC2 instances (SSM access)
resource "aws_iam_role" "rke2_node" {
  name = "${var.project}-rke2-node"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.rke2_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr_readonly" {
  role       = aws_iam_role.rke2_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# S3 access for RKE2 artifacts and data
resource "aws_iam_role_policy" "s3_access" {
  name = "${var.project}-s3-access"
  role = aws_iam_role.rke2_node.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3BucketManagement"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:DeleteBucket",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetBucketVersioning",
          "s3:PutBucketVersioning"
        ]
        Resource = "arn:aws:s3:::${var.project}-*"
      },
      {
        Sid    = "S3ObjectAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.project}-*",
          "arn:aws:s3:::${var.project}-*/*"
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "rke2_node" {
  name = "${var.project}-rke2-node"
  role = aws_iam_role.rke2_node.name
}

# -----------------------------------------------------------------------------
# Bastion Host
# -----------------------------------------------------------------------------

resource "aws_instance" "bastion" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.bastion_instance_type
  key_name                    = var.ssh_key_name
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.bastion.id]
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.rke2_node.name

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile("${path.module}/templates/bastion-userdata.sh.tftpl", {
    install_cloudflared = var.ingress_provider == "cloudflare"
    tunnel_token        = var.ingress_provider == "cloudflare" ? cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].tunnel_token : ""
  })

  tags = merge(local.common_tags, {
    Name = "${var.project}-bastion"
    Role = "bastion"
  })
}

# -----------------------------------------------------------------------------
# RKE2 Control Plane Nodes
# -----------------------------------------------------------------------------

resource "aws_instance" "control_plane" {
  count                  = var.control_plane_count
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.control_plane_instance_type
  key_name               = var.ssh_key_name
  subnet_id              = aws_subnet.private[count.index % length(aws_subnet.private)].id
  vpc_security_group_ids = [aws_security_group.control_plane.id]
  iam_instance_profile   = aws_iam_instance_profile.rke2_node.name

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, {
    Name                     = "${var.project}-control-plane-${count.index + 1}"
    Role                     = "control-plane"
    "kubernetes.io/cluster/${var.project}" = "owned"
  })
}

# -----------------------------------------------------------------------------
# RKE2 Worker Nodes
# -----------------------------------------------------------------------------

resource "aws_instance" "worker" {
  count                  = var.worker_count
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.worker_instance_type
  key_name               = var.ssh_key_name
  subnet_id              = aws_subnet.private[count.index % length(aws_subnet.private)].id
  vpc_security_group_ids = [aws_security_group.worker.id]
  iam_instance_profile   = aws_iam_instance_profile.rke2_node.name

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  # Data volume for Dask local storage
  ebs_block_device {
    device_name = "/dev/sdf"
    volume_size = var.data_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, {
    Name                     = "${var.project}-worker-${count.index + 1}"
    Role                     = "worker"
    "kubernetes.io/cluster/${var.project}" = "owned"
  })
}

# -----------------------------------------------------------------------------
# Network Load Balancer for Kubernetes API
# -----------------------------------------------------------------------------

resource "aws_lb" "k8s_api" {
  name               = "${var.project}-k8s-api"
  internal           = true
  load_balancer_type = "network"
  subnets            = aws_subnet.private[*].id

  tags = merge(local.common_tags, {
    Name = "${var.project}-k8s-api-nlb"
  })
}

resource "aws_lb_target_group" "k8s_api" {
  name     = "${var.project}-k8s-api"
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

  tags = local.common_tags
}

resource "aws_lb_listener" "k8s_api" {
  load_balancer_arn = aws_lb.k8s_api.arn
  port              = 6443
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.k8s_api.arn
  }
}

resource "aws_lb_target_group_attachment" "k8s_api" {
  count            = var.control_plane_count
  target_group_arn = aws_lb_target_group.k8s_api.arn
  target_id        = aws_instance.control_plane[count.index].id
  port             = 6443
}

# RKE2 supervisor API target group (for agent registration)
resource "aws_lb_target_group" "rke2_supervisor" {
  name     = "${var.project}-rke2-supervisor"
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

  tags = local.common_tags
}

resource "aws_lb_listener" "rke2_supervisor" {
  load_balancer_arn = aws_lb.k8s_api.arn
  port              = 9345
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.rke2_supervisor.arn
  }
}

resource "aws_lb_target_group_attachment" "rke2_supervisor" {
  count            = var.control_plane_count
  target_group_arn = aws_lb_target_group.rke2_supervisor.arn
  target_id        = aws_instance.control_plane[count.index].id
  port             = 9345
}
