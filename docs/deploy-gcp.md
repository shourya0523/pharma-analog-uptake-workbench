# Deploying on one Google Cloud e2-micro

One VM runs the API, the built UI and a reverse proxy that terminates TLS.
Everything is on the same origin, so there is no CORS list to keep, no
mixed-content problem, and nothing to rebuild when a URL changes.

Follow this top to bottom on a box with nothing on it and you will finish with
an HTTPS URL that survives a reboot.

---

## Why this shape

Jobs run inside the web process. `POST /runs` returns immediately and
`InProcessJobQueue` runs each job as an asyncio task that takes minutes; a host
that sleeps the process or throttles CPU after the response kills jobs
mid-run, which rules out the serverless options. A plain VM does not do that.

The database is SQLite on the instance's disk rather than a Postgres container,
because a second database process would take a real slice of the single
gigabyte and the code already handles SQLite's one-writer rule - see the
`sqlite_connect_args` docstring and the `write_lock_held_by` diagnostic in
`backend/app/db/models.py`.

A restart is safe: `recover_stranded_jobs` in `backend/app/main.py` requeues
the jobs that had not started and fails the ones that were mid-flight, so a
deploy never leaves rows stuck in `running`.

---

## What the free tier actually covers

From [Google's free tier
page](https://docs.cloud.google.com/free/docs/free-cloud-features):

- 1 non-preemptible `e2-micro` per month in `us-west1`, `us-central1` or
  `us-east1`
- 30 GB-months standard persistent disk
- 1 GB outbound data transfer from North America per month

This guide uses **`us-central1`**. There is no expiry and no idle-reclamation
clause.

### The one cost that is not settled

An in-use external IPv4 address on a standard VM is
[$0.005 per hour](https://cloud.google.com/vpc/pricing-announce-external-ips)
- about $3.65 a month. Neither that pricing page nor the free tier page above
states an exemption for the free tier or for `e2-micro`. Some third-party
write-ups claim one exists; Google's own pages, checked while writing this, do
not say so either way.

Treat the box as free-except-possibly-the-IP and look at your first monthly
bill rather than trusting this paragraph. Nothing else here leaves the
allowance as long as egress stays under 1 GB.

### Staying inside 1 GB of egress

The built bundle is the largest single thing you send. The proxy compresses it
(`encode zstd gzip` in `deploy/Caddyfile`), which at the time of writing takes
one cold page load from 694 kB to 203 kB - roughly 5,000 cold loads a month
before the allowance is gone. Re-run `npm run build` in `frontend/` to see the
current figure. API responses are JSON and the review queue pages, so they are
small by comparison.

---

## 1. Create the instance

You need a Google Cloud project with a billing account attached; the free tier
is not available without one. Either the console or `gcloud` works - the CLI
version is here because it is the one that can be pasted.

Reserve a static address first. An ephemeral one changes when the VM is stopped
and started, which would invalidate both the hostname and the certificate.

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud config set compute/region us-central1

gcloud compute addresses create workbench-ip --region=us-central1

gcloud compute instances create workbench \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --address=workbench-ip \
  --tags=http-server,https-server
```

Note the external IP it prints; everything below needs it.

```bash
gcloud compute addresses describe workbench-ip --region=us-central1 --format='value(address)'
```

## 2. Open the firewall

The `http-server` and `https-server` tags above only matter if rules exist for
them. On a project that has never served traffic they may not. Create them if
the first command returns nothing:

```bash
gcloud compute firewall-rules list --filter="name~'allow-http|allow-https'"

gcloud compute firewall-rules create default-allow-http \
  --allow=tcp:80 --target-tags=http-server --direction=INGRESS
gcloud compute firewall-rules create default-allow-https \
  --allow=tcp:443 --target-tags=https-server --direction=INGRESS
```

Port 80 is not optional even if you only want HTTPS: Let's Encrypt proves you
control the name by making a plain HTTP request to it.

## 3. Connect and add swap

```bash
gcloud compute ssh workbench --zone=us-central1-a
```

1 GB of RAM is enough to *run* this, but not comfortably enough to *build* the
frontend, which compiles TypeScript and bundles in one pass. Swap costs disk,
which you have 30 GB of, and turns an out-of-memory build failure into a slow
build:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

The `fstab` line is what brings it back after a reboot.

## 4. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker          # or log out and back in
docker compose version
```

## 5. Clone

```bash
sudo mkdir -p /opt && sudo chown $USER /opt
git clone https://github.com/shourya0523/pharma-analog-uptake-workbench.git \
  /opt/pharma-analog-uptake-workbench
cd /opt/pharma-analog-uptake-workbench
```

The path matters only because the systemd unit in step 8 names it. Use a
different one and edit `WorkingDirectory` to match.

## 6. Decide the address

Caddy takes the scheme from `SITE_ADDRESS`. A hostname means it obtains and
renews a Let's Encrypt certificate; anything else means it does not, and a
browser on an HTTPS page will refuse to call a plain-HTTP API - there is no way
around that from the application side.

**With a domain you own.** Add an `A` record pointing at the external IP from
step 1, wait for it to resolve, then use the hostname:

    SITE_ADDRESS=workbench.example.com

**Without one.** `sslip.io` resolves any hostname to the address written into
it, so the VM's own IP becomes a name with no registration and no DNS to
manage. Substitute your IP with dots replaced by hyphens - an address like
`203.0.113.9` gives:

    SITE_ADDRESS=203-0-113-9.sslip.io

Check it resolves to your box before going on:

```bash
getent hosts 203-0-113-9.sslip.io
```

Two things to know about this shortcut. `sslip.io` is not on the [public suffix
list](https://publicsuffix.org/list/public_suffix_list.dat), so Let's Encrypt
counts every `sslip.io` name in the world against one registered-domain rate
limit; issuance can fail for reasons that have nothing to do with you, in which
case wait or use a real domain. And the name contains the IP, so changing the
IP changes the URL. A domain you own is the better answer as soon as you have
one - only `SITE_ADDRESS` changes, and Caddy gets the new certificate by
itself.

**No certificate at all**, for a first look before DNS is sorted:

    SITE_ADDRESS=:80

## 7. Write the env file and start

```bash
cp deploy/env.example deploy/.env
chmod 600 deploy/.env
nano deploy/.env
```

Three things must be set:

- `SITE_ADDRESS` - from step 6.
- `OPENROUTER_API_KEY` - from https://openrouter.ai/keys. This file is
  gitignored and is the only place the key lives. It is never in the compose
  file, never in the image, and never committed.
- `SEC_USER_AGENT` - SEC's fair-access policy requires a real contact address
  on every request and blocks by user agent rather than answering. Put a
  mailbox you read.

Leave `MAX_CONCURRENT_JOBS=1`. Base memory is around 150 MB with roughly 70 MB
more while a large annual report is being parsed; four concurrent jobs were
measured at 264 MB resident. Two is survivable on this box if you watch
`docker stats`. One is the setting that does not need watching.

Then:

```bash
cd deploy
docker compose up -d --build
```

The first build compiles the frontend and installs the Python environment, and
on two shared vCPUs it is slow - ten to twenty minutes is normal. Watch it:

```bash
docker compose logs -f
```

The API runs its migrations at startup against an empty database, so the first
boot walks every revision before it answers.

## 8. Survive a reboot

`restart: unless-stopped` in the compose file already brings the containers
back when the Docker daemon starts. The systemd unit covers the other case -
containers removed rather than stopped - and gives you start/stop by name:

```bash
cd /opt/pharma-analog-uptake-workbench
sudo cp deploy/pharma-workbench.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pharma-workbench
```

Prove it rather than assume it:

```bash
sudo reboot
# wait, reconnect
curl -sS https://YOUR_SITE_ADDRESS/api/health
```

`{"status":"ok","environment":"gcp"}` means the whole chain came back.

## 9. Check it end to end

Open `https://YOUR_SITE_ADDRESS/` in a browser. Add a product, watch the run on
the monitor page, and open its figures when it finishes. If the UI loads but
every call fails, the proxy is up and the API is not - `docker compose logs
api` will say why.

---

## Keeping it running

### Pruning old runs

A 24-product sweep fetches roughly 2 GB of filings. 30 GB holds several, then
it does not. `scripts/prune_storage.py` removes the stored documents of runs
that have not been touched recently. It needs nothing but the system `python3`
and it is safe to run while the stack is up.

It prints what it would remove and removes nothing until you pass `--apply`:

```bash
cd /opt/pharma-analog-uptake-workbench
python3 scripts/prune_storage.py deploy/storage --older-than 30
python3 scripts/prune_storage.py deploy/storage --older-than 30 --apply
```

Run directories hold one run's own pages and nothing else reads them once its
figures are reviewed. The document cache is separate and shared - it is keyed
by accession, so two products citing one filing read the same bytes, and
deleting it costs a refetch rather than an answer. That is why it is only
touched when you ask:

```bash
python3 scripts/prune_storage.py deploy/storage \
  --older-than 30 --cache-older-than 90 --apply
```

The database and its write-ahead log are files at the root of that directory
and are never candidates.

Monthly, from cron:

```bash
crontab -e
# 04:00 on the first of the month
0 4 1 * * cd /opt/pharma-analog-uptake-workbench && /usr/bin/python3 scripts/prune_storage.py deploy/storage --older-than 60 --apply >> /var/log/workbench-prune.log 2>&1
```

Check the disk with `df -h /` and the storage root with
`du -sh deploy/storage/*`.

### Updating

```bash
cd /opt/pharma-analog-uptake-workbench
git pull
cd deploy && docker compose up -d --build
```

Interrupted jobs are requeued or failed with a reason at startup, so a deploy
does not strand rows.

### Backing up

Everything that matters is under `deploy/storage`. The database is the small
part of it and the part you cannot refetch:

```bash
docker compose exec api python -c \
  "import sqlite3; s=sqlite3.connect('/app/storage/workbench.db'); \
   d=sqlite3.connect('/app/storage/backup.db'); s.backup(d); d.close()"
```

Copying the file directly while the stack is running gives you a torn database;
the `backup()` call above does not. Pull it off the box with
`gcloud compute scp`.

### Logs

```bash
docker compose logs -f api
docker compose logs -f web
```

Capped at 10 MB × 3 files per service in the compose file, so they cannot fill
the disk.

---

## When it does not work

**The certificate never arrives.** `docker compose logs web` carries the ACME
error. The usual causes are DNS not yet pointing at the box, port 80 closed
(step 2 - Let's Encrypt needs it even for an HTTPS-only site), or the
`sslip.io` rate limit described in step 6. Caddy retries by itself; the site
serves plain HTTP in the meantime.

**The UI loads and every request fails.** The API is down or still migrating.
`docker compose logs api`. On a first boot give it a minute.

**The first build is killed.** Out of memory - step 3's swapfile is missing or
was not enabled. `free -h` should show 2 GB of swap.

**Jobs die part way through.** Check `docker stats` while one runs. If the API
container is near the box's memory, `MAX_CONCURRENT_JOBS` is above 1.

**The disk is full.** `df -h /`, then prune. Filings dominate; the cache is the
big one and it is refetchable.

---

## Moving the frontend off the box later

Firebase Hosting or Vercel would serve the bundle and stop it counting against
the 1 GB. It does not remove the need for a certificate on the VM, because the
API still has to answer an HTTPS page - and it brings back the thing this
layout avoids: `cors_origins` in `backend/app/config.py` is an exact list read
in `backend/app/main.py`, with no `allow_origin_regex`, so every origin that
serves the UI has to be named there, including preview deployments. Weigh that
against 203 kB a load.
