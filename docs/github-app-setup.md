# Cryovial GitHub App Setup

Each cryovial host gets its own GitHub App for downloading release assets
from private repos. Per-host apps provide isolation — revoking one host's
access doesn't affect others.

## Overview

```
GitHub App (per host)           cryovial on host
┌──────────────────┐            ┌─────────────────┐
│ cryovial-waxpool │            │ PEM key file     │
│ org: LaconicNet  │───────────▶│ /etc/cryovial/   │
│ installed on:    │            │   github-app.pem │
│   mark-2-marq    │            │                  │
│   rjk-laconic    │            │ JWT → token →    │
└──────────────────┘            │ download binary  │
                                └─────────────────┘
```

## Step 1: Create the GitHub App

1. Go to **https://github.com/organizations/LaconicNetwork/settings/apps/new**
   (must be org owner)

2. Fill in:
   - **GitHub App name**: `cryovial-<hostname>` (e.g. `cryovial-waxpool`)
   - **Homepage URL**: `https://github.com/LaconicNetwork/cryovial`
   - **Webhook**: uncheck "Active" (we don't need webhook events)

3. **Permissions** → Repository permissions:
   - **Contents**: Read-only (this is all we need for release downloads)
   - Leave everything else as "No access"

4. **Where can this GitHub App be installed?**: "Any account"
   (allows installing on multiple orgs, e.g. mark-2-marquette + rjk-laconic)

5. Click **Create GitHub App**

6. Note the **App ID** shown on the app's settings page (a number like `123456`)

## Step 2: Generate the private key

1. On the app's settings page, scroll to **Private keys**
2. Click **Generate a private key**
3. A `.pem` file downloads — this is the signing key for JWT tokens
4. Save it securely. You'll deploy it to the host in step 4.

## Step 3: Install the app on target orgs

1. Go to the app's settings page → **Install App** (left sidebar)
2. Click **Install** next to each org the host needs access to
   (e.g. `mark-2-marquette`)
3. Choose **Only select repositories** and pick the repos with releases
   (or "All repositories" if the host needs access to everything)
4. Note the **Installation ID** from the URL after installing:
   `https://github.com/organizations/<org>/settings/installations/<INSTALLATION_ID>`

## Step 4: Deploy to the host

Copy the PEM to the host:

```bash
scp <downloaded-pem-file> <user>@<host>:/etc/cryovial/github-app.pem
ssh <user>@<host> 'sudo chmod 600 /etc/cryovial/github-app.pem'
```

Add the app ID and installation ID to `/etc/cryovial/env`:

```
GITHUB_APP_ID=<app-id-from-step-1>
GITHUB_APP_INSTALLATION_ID=<installation-id-from-step-3>
GITHUB_APP_PEM=/etc/cryovial/github-app.pem
```

Reinstall cryovial (to pick up PyJWT dependency) and restart:

```bash
uv tool install --force --reinstall git+https://github.com/LaconicNetwork/cryovial.git@main
sudo systemctl restart cryovial
```

## Step 5: Verify

Test a deploy from the host:

```bash
SECRET=$(sudo grep CRYOVIAL_SECRET /etc/cryovial/env | cut -d= -f2)
curl -s -X POST http://localhost:8090/deploy/notify \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"service":"<service-name>","image":"<release-tag>"}'
```

Check the deploy record:

```bash
ls ~/.cryovial/deploys/
cat ~/.cryovial/deploys/<deploy-id>.yml
```

If `status: completed`, the auth is working. If `status: failed` with
`HTTP Error 404`, the app doesn't have access to the repo or the URL
template is wrong. Check the installation scope in step 3.

## Adding a new host

Repeat steps 1-4 with a new app name (e.g. `cryovial-biscayne`). Each
host gets its own app and PEM — never share PEM keys between hosts.

## Revoking access

To revoke a host's access:
1. Go to the org's **Settings → Integrations → GitHub Apps**
2. Find the host's app installation and click **Configure**
3. Click **Uninstall** (or **Suspend** for temporary)

The host's PEM becomes useless — it can't generate valid tokens without
an active installation.

## Ansible automation

The cryovial ansible role supports the GitHub App config:

```yaml
- role: cryovial_service
  vars:
    cryovial_service_secret: "{{ cryovial_secret }}"
    cryovial_service_github_app_id: "123456"
    cryovial_service_github_app_installation_id: "789012"
    cryovial_service_github_app_pem: "/etc/cryovial/github-app.pem"
```

The PEM file must be deployed separately (not in the role — secrets
should not be in ansible variables).
