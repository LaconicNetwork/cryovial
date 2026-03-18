# Cryovial

Host-resident deploy service for container clusters. Receives webhook
notifications from CI, triggers laconic-so deployment restarts. Pods
pull new images from GHCR directly (imagePullPolicy: Always).

## Architecture

```
GitHub Actions → POST /deploy/notify → cryovial → laconic-so restart → k8s pulls from GHCR
```

Cryovial is an independent service, not part of exophial. It runs on the
bare host (systemd) because it needs access to laconic-so, which manages
the kind cluster. It does not run inside a container or k8s pod.

### What cryovial does

1. Listens on port 8090 for POST /deploy/notify with Bearer auth
2. Looks up the service name in services.yml to find the deployment dir
3. Runs `laconic-so deployment --dir <dir> restart` from the repo dir
4. laconic-so does git pull (to get latest spec), then restarts pods
5. k8s pods pull the latest image from GHCR on restart

### What cryovial does NOT do

- No docker pull or kind load — pods pull from GHCR directly
- No deploy records or rollback logic
- No image watching or polling
- No k8s manifest manipulation — that's laconic-so's job

## Deployment on woodburn

### Prerequisites

1. **uv** installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`

2. **Git HTTPS credentials** for GitHub (laconic-so does git pull during restart):
   ```bash
   # Rewrite SSH URLs to HTTPS (gor-deploy gitconfig)
   git config --global url.'https://github.com/'.insteadOf 'git@github.com:'

   # Credential helper reads PAT from credentials file
   cat > ~/.git-credential-helper.sh << 'EOF'
   #!/bin/bash
   echo "username=jenkins-vulcanize"
   echo "password=$(cat ~/.credentials/woodburn-deployer.txt)"
   EOF
   chmod +x ~/.git-credential-helper.sh
   git config --global credential.helper '/home/gor-deploy/.git-credential-helper.sh'
   ```
   The PAT belongs to `jenkins-vulcanize` (GitHub machine user) with `repo` scope
   for gorbagana-dev org access.

3. **Webhook secret** at `~/.credentials/cryovial-webhook-secret`

4. **Firewall** port 8090 open: `sudo firewall-cmd --zone=public --add-port=8090/tcp --permanent`

### Install and run

```bash
# Install
uv tool install git+https://github.com/AFDudley/cryovial.git

# Update
cryovial self-update

# The ansible playbook handles systemd setup:
# woodburn_deployer/ansible/playbooks/gor_infra/deploy_cryovial.yml
```

### Services config

`~/.config/cryovial/services.yml` maps service names to laconic-so deployments:

```yaml
services:
  dumpster-backend:
    stack_name: /home/gor-deploy/deployments/dumpster-deployment
    repo_dir: /home/gor-deploy/deployments/dumpster-stack
```

- `stack_name`: laconic-so deployment directory (passed to `--dir`)
- `repo_dir`: git repo containing the stack (cwd for laconic-so, so
  relative `stack-source` paths in deployment.yml resolve correctly)

### GitHub Actions integration

Org-level secrets in gorbagana-dev:
- `DEPLOY_WEBHOOK_URL`: `http://woodburn.vaasl.io:8090` (workflows append `/deploy/notify`)
- `DEPLOY_WEBHOOK_SECRET`: Bearer token matching `~/.credentials/cryovial-webhook-secret`

CI workflow step:
```yaml
- name: Notify deploy webhook
  continue-on-error: true
  run: |
    curl -sf -X POST "${{ secrets.DEPLOY_WEBHOOK_URL }}/deploy/notify" \
      -H "Authorization: Bearer ${{ secrets.DEPLOY_WEBHOOK_SECRET }}" \
      -H "Content-Type: application/json" \
      -d '{"service":"dumpster-backend"}'
```

### Systemd service

```
/etc/systemd/system/cryovial.service
```

Runs as gor-deploy. PATH includes `~/.local/bin` (cryovial) and
`~/.venv/laconic-so/bin` (laconic-so). Restarts on failure.

### Known issues

- **cv-a1a**: Must check for terminating namespace before restart.
  Rapid restarts fail if the previous namespace is still cleaning up.

## Related systems

- **exophial** — development coordination (dispatcher, tasks, agents). Separate tool.
- **pellicle** — host access control layer (future). Cryovial is the foundation
  pellicle builds on. See `exophial/docs/v2.0.0/PELLICLE.md`.
- **laconic-so** — cluster lifecycle management. Cryovial delegates all k8s
  operations to laconic-so.
- **woodburn_deployer** — ansible playbooks for woodburn infrastructure.
  `deploy_cryovial.yml` installs and configures cryovial.
