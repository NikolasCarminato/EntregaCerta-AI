terraform {
  required_version = ">= 1.14, < 2.0"
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "4.7.0"
    }
  }
}

# gerenciar.py descobre os recursos existentes e preenche estas variáveis.
variable "config" {
  type = object({
    raiz            = string
    contexto_docker = string
    imagem_app      = string
    imagem_postgres = string
    rede            = string
    volume          = string
    usuario         = string
    banco           = string
    porta_app       = number
  })
}

variable "senha_postgres" {
  type      = string
  sensitive = true
}

provider "docker" {
  context = var.config.contexto_docker
}

# O banco atual, a rede e o volume são IMPORTADOS, nunca recriados na adoção.
# ignore_changes preserva inclusive os atributos definidos pelo Compose.
resource "docker_network" "rede" {
  name = var.config.rede
  lifecycle {
    prevent_destroy = true
    ignore_changes  = all
  }
}

resource "docker_volume" "dados" {
  name = var.config.volume
  lifecycle {
    prevent_destroy = true
    ignore_changes  = all
  }
}

resource "docker_container" "postgres" {
  name           = "entregacerta_ai_postgres_db"
  image          = var.config.imagem_postgres
  restart        = "unless-stopped"
  remove_volumes = false
  env = [
    "POSTGRES_USER=${var.config.usuario}",
    "POSTGRES_DB=${var.config.banco}",
    "POSTGRES_PASSWORD=${var.senha_postgres}",
  ]
  ports {
    internal = 5432
    external = 5433
    ip       = "127.0.0.1"
  }
  volumes {
    volume_name    = docker_volume.dados.name
    container_path = "/var/lib/postgresql/data"
  }
  networks_advanced {
    name    = docker_network.rede.name
    aliases = ["postgres"]
  }
  lifecycle {
    prevent_destroy = true
    ignore_changes  = all
  }
}

resource "docker_container" "aplicacao" {
  name    = "entregacerta_ai_app"
  image   = var.config.imagem_app
  restart = "unless-stopped"
  env = [
    "POSTGRES_HOST=${docker_container.postgres.name}",
    "POSTGRES_PORT=5432",
    "ENTREGACERTA_DATABASE_URL=",
    "ENTREGACERTA_COOKIE_SECURE=false",
    "ENTREGACERTA_ORIGINS=http://127.0.0.1:${var.config.porta_app},http://localhost:${var.config.porta_app}",
  ]
  ports {
    internal = 8000
    external = var.config.porta_app
    ip       = "127.0.0.1"
  }
  # Senhas/chaves não entram na imagem nem nas variáveis do contêiner Terraform.
  volumes {
    host_path      = "${var.config.raiz}/.env"
    container_path = "/app/.env"
    read_only      = true
  }
  networks_advanced {
    name = docker_network.rede.name
  }
  wait         = true
  wait_timeout = 180
}

output "url" {
  value = "http://localhost:${var.config.porta_app}"
}
