# Secrets Manager entries for the database URL and the JWT signing key.
# The API and worker tasks read these at boot; nothing is baked into images.

resource "random_password" "db" {
  length  = 32
  special = false # keep the connection string URL-safe
}

resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "db_url" {
  name        = "${local.prefix}/database-url"
  description = "BluCheck SQLAlchemy database URL"
  kms_key_id  = aws_kms_key.main.arn
}

resource "aws_secretsmanager_secret_version" "db_url" {
  secret_id = aws_secretsmanager_secret.db_url.id
  secret_string = format(
    "postgresql+psycopg://%s:%s@%s:%d/%s",
    var.db_username,
    random_password.db.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
    var.db_name,
  )
}

resource "aws_secretsmanager_secret" "jwt" {
  name        = "${local.prefix}/jwt-secret"
  description = "BluCheck JWT signing key"
  kms_key_id  = aws_kms_key.main.arn
}

# RunPod API key + VLM endpoint id. Created out of band (scripts) with the AWS-managed
# secrets key; referenced here so the worker task role can read it.
data "aws_secretsmanager_secret" "runpod" {
  name = "${local.prefix}/runpod"
}

# Firebase service-account key for FCM v1 push. Created out of band (from the Firebase
# console key); referenced so the api task role can read it to send background pushes.
data "aws_secretsmanager_secret" "fcm" {
  name = "${local.prefix}/fcm"
}

resource "aws_secretsmanager_secret_version" "jwt" {
  secret_id     = aws_secretsmanager_secret.jwt.id
  secret_string = random_password.jwt.result
}
