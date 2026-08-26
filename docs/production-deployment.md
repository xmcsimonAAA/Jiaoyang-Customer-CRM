# `crm.muskzoom.com` Production Deployment

This runbook deploys the customer module on the existing GoDaddy Ubuntu VPS without
changing the current MuskZoom process or its SQLite business database. The integration
point is MuskZoom account identity and one-time SSO only.

## Before touching the server

1. A public GitHub repository is acceptable only for reviewed source code. Never upload
   `.env`, `customer_data.db`, `backups/`, customer spreadsheets, or exported customer files.
2. Commit the reviewed customer-system code and push it to the public repository.
3. The VPS can clone this public repository over HTTPS; no GitHub token or deploy key is
   needed for read-only pulls. Never configure a personal GitHub token on the server.
4. In Cloudflare, add an `A` record: `crm` -> the VPS public IP. Leave it **DNS only**
   until the TLS certificate is issued.
5. Generate two independent secrets on a trusted computer:

   ```bash
   openssl rand -hex 32
   openssl rand -hex 32
   ```

   One is `MUSKZOOM_SSO_SECRET`; the other is `MUSKZOOM_IDENTITY_SECRET`. Never reuse
   the existing channel-module secret and never place either value in Git.

## 1. Prepare Ubuntu once

Run as an administrative sudo user. These commands install PostgreSQL and the Python
runtime only; they do not restart or replace the existing MuskZoom service.

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib python3-venv python3-pip certbot python3-certbot-nginx git
sudo adduser --system --group --home /opt/jy-customer --shell /usr/sbin/nologin jycrm
sudo install -d -o jycrm -g jycrm -m 0750 /opt/jy-customer /var/backups/jy-customer
sudo install -d -o root -g root -m 0755 /var/www/certbot /etc/jy-customer
```

The VPS has 4 GB RAM and no swap. Add a 2 GB swap file before adding PostgreSQL:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 2. Create the isolated PostgreSQL database

Use a long random password. The database must listen only locally; do not expose port
`5432` in a firewall or Cloudflare rule.

```bash
sudo -u postgres psql
CREATE USER jy_crm WITH LOGIN PASSWORD 'replace-with-a-long-random-password';
CREATE DATABASE jy_customer_crm OWNER jy_crm ENCODING 'UTF8';
\q
```

## 3. Put the application on the VPS

Use the public repository created above. The application data and production secrets remain
on the VPS; they are not stored in GitHub.

```bash
sudo -u jycrm git clone https://github.com/xmcsimonAAA/Jiaoyang-Customer-CRM.git /opt/jy-customer
sudo -u jycrm python3 -m venv /opt/jy-customer/.venv
sudo -u jycrm /opt/jy-customer/.venv/bin/pip install --upgrade pip
sudo -u jycrm /opt/jy-customer/.venv/bin/pip install -r /opt/jy-customer/requirements.txt
sudo install -o jycrm -g jycrm -m 0600 /opt/jy-customer/deploy/production.env.example /etc/jy-customer/jy-customer.env
sudoedit /etc/jy-customer/jy-customer.env
```

Edit the environment file with the real PostgreSQL URL and the two generated secrets.
Do not set `MUSKZOOM_DB_PATH` in production. `CRM_ALLOW_PASSWORD_LOGIN` must remain
`false`, and `CRM_COOKIE_SECURE` must remain `true`.

## 4. Issue the certificate and configure Nginx

First wait until `dig crm.muskzoom.com +short` returns the VPS IP. Install the temporary
configuration, test it, and issue the certificate:

```bash
sudo cp /opt/jy-customer/deploy/nginx/crm.muskzoom.com.bootstrap.conf /etc/nginx/sites-available/crm.muskzoom.com
sudo ln -s /etc/nginx/sites-available/crm.muskzoom.com /etc/nginx/sites-enabled/crm.muskzoom.com
sudo nginx -t
sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/certbot -d crm.muskzoom.com
```

Then install the final reverse-proxy configuration:

```bash
sudo cp /opt/jy-customer/deploy/nginx/crm.muskzoom.com.conf /etc/nginx/sites-available/crm.muskzoom.com
sudo nginx -t
sudo systemctl reload nginx
```

In Cloudflare, set SSL/TLS encryption mode to **Full (strict)**, then turn the `crm`
record proxy back on. Keep HTTPS enforcement enabled.

## 5. Migrate the final data snapshot

Do this only after the MuskZoom SSO/identity changes in the next section have been
reviewed, but before opening the customer module to staff.

1. Stop local customer-data entry for a short migration window.
2. Make a final SQLite backup on the development machine.
3. Copy that one backup file to the VPS using an encrypted transfer. Do not use email or
   public file-sharing links.
4. First perform a dry check on the VPS:

   ```bash
   sudo -u jycrm /opt/jy-customer/.venv/bin/python /opt/jy-customer/backend/scripts/migrate_sqlite_to_postgres.py \
     --source /secure-transfer/customer_data-final.db
   ```

5. After checking the reported counts, run the only write operation:

   ```bash
   sudo -u jycrm /opt/jy-customer/.venv/bin/python /opt/jy-customer/backend/scripts/migrate_sqlite_to_postgres.py \
     --source /secure-transfer/customer_data-final.db --apply
   ```

The script refuses to overwrite a PostgreSQL target containing data. It deliberately
does not migrate login sessions, so every user must enter from MuskZoom after cutover.

## 6. Start the customer service and backups

```bash
sudo cp /opt/jy-customer/deploy/systemd/jy-customer.service /etc/systemd/system/
sudo cp /opt/jy-customer/deploy/systemd/jy-customer-backup.service /etc/systemd/system/
sudo cp /opt/jy-customer/deploy/systemd/jy-customer-backup.timer /etc/systemd/system/
sudo chmod 0750 /opt/jy-customer/deploy/scripts/backup_postgres.sh
sudo systemctl daemon-reload
sudo systemctl enable --now jy-customer.service
sudo systemctl enable --now jy-customer-backup.timer
sudo systemctl status jy-customer.service --no-pager
curl --fail https://crm.muskzoom.com/api/health
```

The backup timer retains 30 daily local backups. Set up a separate encrypted off-server
copy before declaring the system production-ready, and test one restore with
`pg_restore --clean --if-exists` into a temporary database.

## 7. Add MuskZoom integration

MuskZoom must receive a small backend and navigation change:

- `GET /api/integrations/customer-crm/users`: returns only active users with `id`,
  `username`, `name`, `role`, `roleLabel`, `rolePermission`, and `team`; it accepts only
  the `X-Customer-Module-Secret` header and rejects all other callers.
- `GET /api/customer-crm/sso-url`: validates the current MuskZoom session, signs a
  90-second token with `iss=muskzoom`, `aud=jiaoyang-customer-crm`, `iat`, `exp`, `jti`,
  and `username`, then returns `https://crm.muskzoom.com/?sso_token=...`.
- Sidebar action **客户数据**: calls the SSO-url endpoint and redirects the current tab.

The same two secrets must be configured in MuskZoom's server environment under the
customer-specific names. The customer module then caches the identity list for at most
60 seconds, while every browser entry requires a new short-lived one-time SSO token.

## 8. Acceptance and cutover

Before sharing the entry with staff, validate all of the following:

- `/api/health` reports `database: postgresql`.
- An inactive MuskZoom user cannot enter the customer module.
- A business manager sees only owned or collaborative customers.
- A department supervisor sees only their team; administrator and developer see all.
- Import, export, custom-field, customer assignment, advisor binding, and Hongan advisor
  permissions match the approved roles.
- The customer count, TW numbers, holdings, advisor names, and audit-history counts match
  the migration report.
- Mobile entry, filtering, export, backup, and restore all work.

Keep the old local SQLite snapshot read-only for at least 30 days. Do not delete it after
the first successful login.
