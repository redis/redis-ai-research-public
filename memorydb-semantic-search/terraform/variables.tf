variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "ID of the existing VPC to deploy into"
  type        = string
}

variable "private_subnet_a_id" {
  description = "Private subnet (AZ a) for the MemoryDB subnet group"
  type        = string
}

variable "private_subnet_b_id" {
  description = "Private subnet (AZ b) for the MemoryDB subnet group"
  type        = string
}

variable "public_subnet_id" {
  description = "Public subnet for the bastion host"
  type        = string
}

variable "public_route_table_id" {
  description = "Route table associated with the public subnet (for the S3 VPC gateway endpoint)"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC — used to authorize MemoryDB ingress on 6379"
  type        = string
  default     = "10.0.0.0/16"
}

variable "ssh_ingress_cidr" {
  description = "CIDR allowed to SSH into the bastion (your office/home IP, e.g. \"1.2.3.4/32\")"
  type        = string
}

variable "memorydb_cluster_name" {
  description = "Name of the MemoryDB cluster"
  type        = string
  default     = "redis-search"
}

variable "memorydb_node_type" {
  description = "MemoryDB node type"
  type        = string
  default     = "db.r7g.large"
}

variable "memorydb_acl_name" {
  description = "MemoryDB ACL name"
  type        = string
  default     = "open-access"
}

variable "s3_bucket_name" {
  description = "S3 bucket for hybrid session text storage (must be globally unique)"
  type        = string
}

variable "key_pair_name" {
  description = "Name of an EC2 key pair to attach to the bastion"
  type        = string
}

variable "bastion_instance_type" {
  description = "EC2 instance type for the bastion"
  type        = string
  default     = "t4g.small"
}

variable "bastion_availability_zone" {
  description = "AZ for the bastion + its EBS data volume"
  type        = string
  default     = "us-east-1a"
}

variable "ebs_data_size_gb" {
  description = "Size of the bastion's EBS data volume in GiB"
  type        = number
  default     = 10
}
