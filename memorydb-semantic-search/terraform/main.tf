terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_vpc" "stage" {
  id = var.vpc_id
}

data "aws_subnet" "private_a" {
  id = var.private_subnet_a_id
}

data "aws_subnet" "private_b" {
  id = var.private_subnet_b_id
}

resource "aws_memorydb_subnet_group" "main" {
  name       = "${var.memorydb_cluster_name}-subnet-group"
  subnet_ids = [
    data.aws_subnet.private_a.id,
    data.aws_subnet.private_b.id,
  ]
}

resource "aws_security_group" "memorydb" {
  name_prefix = "memorydb-"
  vpc_id      = data.aws_vpc.stage.id

  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_subnet" "public_a" {
  id = var.public_subnet_id
}

data "aws_ami" "al2023_arm" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-kernel-6.18-arm64"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_memorydb_cluster" "main" {
  name                   = var.memorydb_cluster_name
  node_type              = var.memorydb_node_type
  num_shards             = 1
  num_replicas_per_shard = 0
  subnet_group_name      = aws_memorydb_subnet_group.main.name
  security_group_ids     = [aws_security_group.memorydb.id]
  tls_enabled            = true
  acl_name               = var.memorydb_acl_name
  engine                 = "valkey"
  engine_version         = "7.2"
  parameter_group_name   = "default.memorydb-valkey7.search"
}

resource "aws_security_group" "bastion" {
  name        = "bastion-ssh"
  description = "SSH access for Redis bastion"
  vpc_id      = data.aws_vpc.stage.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_vpc_security_group_ingress_rule" "bastion_ssh" {
  security_group_id = aws_security_group.bastion.id
  cidr_ipv4         = var.ssh_ingress_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "bastion_api" {
  security_group_id = aws_security_group.bastion.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 8080
  to_port           = 8080
  ip_protocol       = "tcp"
}

data "aws_route_table" "public" {
  route_table_id = var.public_route_table_id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = data.aws_vpc.stage.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [data.aws_route_table.public.id]
}

resource "aws_s3_bucket" "sessions" {
  bucket        = var.s3_bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "sessions" {
  bucket = aws_s3_bucket.sessions.id
  versioning_configuration {
    status = "Disabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "sessions" {
  bucket = aws_s3_bucket.sessions.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

resource "aws_iam_role" "bastion" {
  name = "bastion-s3"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "bastion_s3" {
  name = "bastion-s3-read"
  role = aws_iam_role.bastion.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.sessions.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.sessions.arn}/*"]
      },
    ]
  })
}

resource "aws_iam_instance_profile" "bastion" {
  name = "bastion-s3"
  role = aws_iam_role.bastion.name
}

resource "aws_ebs_volume" "data" {
  availability_zone = var.bastion_availability_zone
  size              = var.ebs_data_size_gb
  type              = "gp3"
  tags = {
    Name = "bastion-data"
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.bastion.id
}

resource "aws_instance" "bastion" {
  ami                         = data.aws_ami.al2023_arm.id
  instance_type               = var.bastion_instance_type
  subnet_id                   = data.aws_subnet.public_a.id
  associate_public_ip_address = true
  key_name                    = var.key_pair_name
  vpc_security_group_ids      = [aws_security_group.bastion.id]
  iam_instance_profile        = aws_iam_instance_profile.bastion.name

  tags = {
    Name = "redis-bastion"
  }
}
