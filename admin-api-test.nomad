job "admin-api-test" {
  datacenters = ["dc1"]
  type = "service"

  group "admin-api" {
    count = 1

    network {
      mode = "bridge"
    }

    task "server" {
      driver = "docker"

      config {
        image = "vexa_dev-admin-api:latest"
        network_mode = "vexa_dev_vexa_default"
      }

      env {
        DATABASE_URL = "postgresql+asyncpg://postgres:postgres@172.21.0.3:5432/vexa"
        ADMIN_API_TOKEN = "your-secure-admin-token" # This is a placeholder
        LOG_LEVEL = "DEBUG"
      }

      resources {
        cpu    = 256 # MHz
        memory = 128 # MB
      }
    }
  }
} 