# Cryovial

Host-resident deploy service for container clusters. Receives webhook
notifications from CI, triggers laconic-so deployment restarts with
SHA-tagged images for deterministic deploys.

## Architecture

```
GitHub Actions → POST /deploy/notify → cryovial → laconic-so restart → k8s pulls from GHCR
```

Cryovial runs on the bare host (systemd) because it needs access to
laconic-so, which manages the kind cluster. It does not run inside a
container or k8s pod.

### What cryovial does

1. Listens on port 8090 for POST /deploy/notify with Bearer auth
2. Looks up the service name in `services.yml` to find the deployment dir
3. When an `image` field is present, passes the SHA-tagged image to
   `laconic-so deployment --dir <dir> restart --image <name>=<image>`
4. Enforces a per-stack cooldown (429 for 5 minutes after a deploy)
5. Persists deploy records as YAML in `~/.cryovial/deploys/`
6. Returns 202 Accepted immediately; deploy runs in a background thread

### What cryovial does NOT do

- No image watching or polling — CI pushes to cryovial, not the reverse
- No k8s manifest manipulation — that's laconic-so's job
- No rollback logic — deploy records exist for auditability, not recovery

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
uv tool install git+https://github.com/LaconicNetwork/cryovial.git

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

### Deploy records

Each accepted deploy writes a YAML record to `~/.cryovial/deploys/<id>.yml`
with fields: `id`, `service`, `image`, `status` (accepted/completed/failed),
`accepted_at`, `completed_at`, `error`. Records are write-once artifacts
for auditability.

### Known issues

See `.pebbles/` for the current issue tracker.

- **cv-a1a**: Must check for terminating namespace before restart.
  Rapid restarts fail if the previous namespace is still cleaning up.

## Related systems

- **laconic-so** — cluster lifecycle management. Cryovial delegates all k8s
  operations to laconic-so.
- **woodburn_deployer** — ansible playbooks for woodburn infrastructure.
  `deploy_cryovial.yml` installs and configures cryovial.
- **exophial** — development coordination (dispatcher, tasks, agents).
  Cryovial is operationally independent but architecturally part of the
  exophial ecosystem. See `docs/ROADMAP.md` for the evolution plan.
