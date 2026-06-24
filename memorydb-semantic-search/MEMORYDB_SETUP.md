# MemoryDB Setup

## Overview

Amazon MemoryDB cluster provisioned via Terraform. Valkey-compatible, TLS-enabled, single-shard with no replicas. Vector search enabled via `default.memorydb-valkey7.search` parameter group.

All names/IPs/endpoints are placeholders — fill in your own values via `terraform/terraform.tfvars` and `config.yaml`. After `terraform apply`, run `terraform output` to read the actual cluster endpoint and bastion IP, then drop them into `config.yaml`.

## Resources Created

| Resource | Terraform name | Variable controlling it |
|----------|----------------|--------------------------|
| MemoryDB Cluster | `aws_memorydb_cluster.main` | `memorydb_cluster_name`, `memorydb_node_type` |
| Subnet Group | `aws_memorydb_subnet_group.main` | `private_subnet_a_id`, `private_subnet_b_id` |
| Security Group (Redis) | `aws_security_group.memorydb` | `vpc_cidr` (ingress for 6379) |
| EC2 Bastion | `aws_instance.bastion` | `bastion_instance_type`, `key_pair_name` |
| Security Group (Bastion) | `aws_security_group.bastion` | `ssh_ingress_cidr` |
| S3 bucket | `aws_s3_bucket.sessions` | `s3_bucket_name` |
| S3 VPC endpoint | `aws_vpc_endpoint.s3` | `public_route_table_id` |
| EBS volume | `aws_ebs_volume.data` | `ebs_data_size_gb`, `bastion_availability_zone` |

## Endpoint

After `terraform apply`:

```bash
cd terraform && terraform output memorydb_endpoint
# → clustercfg.<your-cluster>.<id>.memorydb.<region>.amazonaws.com:6379
```

Set this as `memorydb.host` in `config.yaml`.

## Steps Taken

1. **Terraform** — Provisioned cluster via `terraform/main.tf`:
   - `aws_memorydb_subnet_group` — subnet group in target VPC
   - `aws_security_group` — port 6379 restricted to `var.vpc_cidr`
   - `aws_memorydb_cluster` — TLS enabled, `open-access` ACL, `default.memorydb-valkey7.search` param group

2. **Node sizing** — Pick `memorydb_node_type` to fit your dataset. `db.r7g.large` (13 GiB) handles the LongMemEval Medium dataset (~3 GB embeddings + text inline).

3. **Vector Search** — Vector index created via:
   ```bash
   FT.CREATE idx ON HASH PREFIX 1 {session}: \
     SCHEMA text TEXT \
     embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 384 DISTANCE_METRIC COSINE
   ```

4. **Semantic Search** — `search.py` uses `FT.SEARCH` with HNSW vector similarity:
   ```
   FT.SEARCH idx "*=>[KNN <k> @embedding $vec EF_RUNTIME 200 AS score]" \
     PARAMS 2 vec <binary> DIALECT 2
   ```

## Config Files

- `terraform/main.tf`, `terraform/variables.tf` — Terraform infrastructure definition
- `terraform/terraform.tfvars.example` — copy to `terraform.tfvars` and fill in
- `config.example.yaml` — copy to `config.yaml` and fill in (Python clients read this)
- `pyproject.toml` — Python dependencies (uv-managed)

## Connect

See [CONNECT_TO_BASTION.md](CONNECT_TO_BASTION.md) — all examples use values from `config.yaml`.

## Useful Commands

```bash
# Check index status
FT.INFO idx

# Vector search (via bastion or Python)
FT.SEARCH idx "*=>[KNN 5 @embedding $vec AS score]" PARAMS 2 vec <binary> DIALECT 2

# List keys
DBSIZE
SCAN 0 MATCH "{session}:*" COUNT 10
```

## Outputs

```bash
cd terraform && terraform output
```

## Teardown

```bash
cd terraform && terraform destroy
```
