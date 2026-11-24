# One-day course demo of the Lore engine on AWS (ap-south-1), as its own throwaway stack.
#   up.sh    builds the bundle (graph, database dump, engine source, UI) and runs `terraform apply`
#   down.sh  runs `terraform destroy`
# With var.hours > 0 the instance also terminates itself after that many hours; the default 0
# keeps it running until down.sh. State is local and gitignored; it holds the env secrets.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.70" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region  = "ap-south-1"
  profile = var.aws_profile
  default_tags { tags = { Project = "lore-course-demo", Owner = "ninad", Lifetime = var.hours > 0 ? "${var.hours}h" : "until-destroyed" } }
}

variable "aws_profile" { default = "loremind" }
variable "host" { default = "lore-demo.loremind.in" }
variable "hours" { default = 0 } # 0: no self-termination; stop with down.sh
variable "instance_type" { default = "t3.large" } # 8 GiB; measured peak 1.2 GiB during a query (2026-11-16)
variable "bundle_dir" { type = string }            # files produced by up.sh
variable "engine_env" {
  type      = string
  sensitive = true
}
variable "caddy_env" {
  type      = string
  sensitive = true
}

resource "random_id" "suffix" { byte_length = 3 }

locals {
  name   = "lore-course-demo-${random_id.suffix.hex}"
  bundle = ["neo4j-data.tgz", "lore_memory.sql.gz", "src.tgz", "ui.tgz", "stack.tgz"]
}

# ---- private bundle bucket ------------------------------------------------------------------
resource "aws_s3_bucket" "bundle" {
  bucket        = local.name
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "bundle" {
  bucket                  = aws_s3_bucket.bundle.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "bundle" {
  for_each = toset(local.bundle)
  bucket   = aws_s3_bucket.bundle.id
  key      = each.value
  source   = "${var.bundle_dir}/${each.value}"
}

# ---- secrets: SSM SecureString, read by the instance role at boot -------------------------------
resource "aws_ssm_parameter" "engine_env" {
  name  = "/${local.name}/engine.env"
  type  = "SecureString"
  value = var.engine_env
}

resource "aws_ssm_parameter" "caddy_env" {
  name  = "/${local.name}/caddy.env"
  type  = "SecureString"
  value = var.caddy_env
}

# ---- instance role: read the bundle and secrets, call Bedrock, be reachable by SSM -------------
resource "aws_iam_role" "demo" {
  name = local.name
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.demo.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "demo" {
  role = aws_iam_role.demo.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"],
      Resource = [aws_s3_bucket.bundle.arn, "${aws_s3_bucket.bundle.arn}/*"] },
      { Effect = "Allow", Action = ["ssm:GetParameter"],
      Resource = [aws_ssm_parameter.engine_env.arn, aws_ssm_parameter.caddy_env.arn] },
      # the apac.* inference profile routes across APAC regions, so model ARNs are not region-pinned
      { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], Resource = "*" },
    ]
  })
}

resource "aws_iam_instance_profile" "demo" {
  name = local.name
  role = aws_iam_role.demo.name
}

# ---- network: only Caddy (80 for the certificate challenge, 443 for the app) ------------------
resource "aws_security_group" "demo" {
  name        = local.name
  description = "Lore course demo: HTTP/HTTPS to Caddy only; admin access via SSM, no SSH"
  ingress {
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
  ingress {
    from_port        = 443
    to_port          = 443
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "demo" {
  ami                                  = data.aws_ssm_parameter.al2023.value
  instance_type                        = var.instance_type
  iam_instance_profile                 = aws_iam_instance_profile.demo.name
  vpc_security_group_ids               = [aws_security_group.demo.id]
  associate_public_ip_address          = true
  instance_initiated_shutdown_behavior = var.hours > 0 ? "terminate" : "stop"
  user_data_replace_on_change          = true
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    bucket = aws_s3_bucket.bundle.id, prefix = "/${local.name}", minutes = var.hours * 60
  })
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers reach IMDS for the instance role (Bedrock)
  }
  root_block_device {
    volume_size = 40
    volume_type = "gp3"
    encrypted   = true
  }
  tags       = { Name = local.name }
  depends_on = [aws_s3_object.bundle, aws_iam_role_policy.demo]
}

# ---- DNS: one record in the existing public zone ----------------------------------------------
data "aws_route53_zone" "root" {
  name         = "loremind.in."
  private_zone = false
}

resource "aws_route53_record" "demo" {
  zone_id = data.aws_route53_zone.root.zone_id
  name    = var.host
  type    = "A"
  ttl     = 60
  records = [aws_instance.demo.public_ip]
}

output "url" { value = "https://${var.host}" }
output "instance_id" { value = aws_instance.demo.id }
output "public_ip" { value = aws_instance.demo.public_ip }
output "self_terminates_after" { value = var.hours > 0 ? "${var.hours}h after boot" : "never; run down.sh" }
