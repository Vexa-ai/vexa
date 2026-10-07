ui = false
api_addr = "http://bao:8200"
unsafe_allow_api_audit_creation = true
cluster_addr = "http://bao:8201"
storage "raft" {
  path = "/bao/data"
  node_id = "mvp-one"
}
listener "tcp" {
  address = "0.0.0.0:8200"
  tls_disable = true
}
