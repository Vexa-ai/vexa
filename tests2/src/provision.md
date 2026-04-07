---
needs: [DEPLOY_TARGET, BRANCH]
gives: [VM_IP, VM_ID, SSH, K8S_NAMESPACE, GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL, USER_ID, API_TOKEN]
---

use: lib/vm
use: env

# Provision

> **Why:** Multiple cookbooks need "fresh infra + deploy + health check". This is that, once.
> **What:** Provision throwaway infra (VM or K8s namespace), clone, deploy, wait healthy, create test user.
> **How:** VM for compose/lite, namespace for helm. Then call src/infra and src/api.

## state

    VM_IP         = ""
    VM_ID         = ""
    SSH           = ""
    K8S_NAMESPACE = ""

## steps

```
1. provision
   if DEPLOY_TARGET in ["compose", "lite"]:
       call: vm.provision(LABEL="vexa-{DEPLOY_TARGET}-{RUN_NUMBER}", REGION={VM_REGION}, TYPE={VM_TYPE}, IMAGE={VM_IMAGE})
       => VM_IP, VM_ID
       => SSH = "ssh -o StrictHostKeyChecking=no root@{VM_IP}"
       call: vm.wait_ssh(IP={VM_IP})
       on_fail: stop

   if DEPLOY_TARGET == "helm":
       => K8S_NAMESPACE = "vexa-test-{RUN_NUMBER}"
       do: kubectl create namespace {K8S_NAMESPACE}
       on_fail: stop

2. clone
   if DEPLOY_TARGET in ["compose", "lite"]:
       do: {SSH} "apt-get update -qq && apt-get install -y -qq git docker.io docker-compose-plugin make"
       do: {SSH} "git clone --branch {BRANCH} {REPO_URL} /root/vexa"
       expect: exits 0
   on_fail: stop

3. deploy
   if DEPLOY_TARGET == "compose":
       do: {SSH} "cd /root/vexa/deploy/compose && make build && make up && make setup-api-key"
       expect: exits 0

   if DEPLOY_TARGET == "lite":
       do: |
           {SSH} "
             cd /root/vexa
             TAG=\$(date +%y%m%d-%H%M)
             docker build -f deploy/lite/Dockerfile.lite -t vexaai/vexa-lite:\$TAG .
             docker run -d --name vexa --shm-size=2g --network host \
               -v /var/run/docker.sock:/var/run/docker.sock \
               vexaai/vexa-lite:\$TAG
           "
       expect: exits 0

   if DEPLOY_TARGET == "helm":
       do: helm install vexa deploy/helm/charts/vexa -n {K8S_NAMESPACE} --set image.tag={BRANCH}
       expect: exits 0
   on_fail: stop

4. wait_healthy
   do: |
       for i in $(seq 1 30); do
           if DEPLOY_TARGET in ["compose", "lite"]:
               {SSH} "curl -sf http://localhost:8056/ > /dev/null 2>&1" && echo "HEALTHY" && break
           if DEPLOY_TARGET == "helm":
               kubectl -n {K8S_NAMESPACE} get pod -l app=api-gateway -o jsonpath='{.items[0].status.phase}' | grep -q Running && echo "HEALTHY" && break
           sleep 10
       done
   expect: HEALTHY
   on_fail: stop

5. infra_and_api
   call: src/infra
   => GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL
   call: src/api
   => USER_ID, API_TOKEN
   on_fail: stop
```
