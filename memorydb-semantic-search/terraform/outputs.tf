output "cluster_endpoint" {
  value = aws_memorydb_cluster.main.cluster_endpoint
}

output "bastion_public_ip" {
  value = aws_instance.bastion.public_ip
}

output "bastion_ssh_command" {
  value = "ssh -i ~/.ssh/${var.key_pair_name}.pem ec2-user@${aws_instance.bastion.public_ip}"
}

output "bastion_port_forward_command" {
  value = "ssh -i ~/.ssh/${var.key_pair_name}.pem -L 6379:${aws_memorydb_cluster.main.cluster_endpoint[0].address}:6379 ec2-user@${aws_instance.bastion.public_ip}"
}

output "bastion_api_url" {
  value = "http://${aws_instance.bastion.public_ip}:8080"
}

output "memorydb_endpoint" {
  value = "${aws_memorydb_cluster.main.cluster_endpoint[0].address}:${aws_memorydb_cluster.main.cluster_endpoint[0].port}"
}

output "sessions_bucket" {
  value = aws_s3_bucket.sessions.bucket
}
