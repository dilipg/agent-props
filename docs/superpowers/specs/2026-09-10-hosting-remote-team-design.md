# Design: host agent-props for a remote team

Date: 2026-09-10 · Status: awaiting owner review · Supersedes nothing

## The goal

Teammates on other machines reach one shared agent-props: their agents and harnesses call the
MCP endpoint, and people open the review dashboard in a browser. Nothing is exposed to the public
internet, and the database is writable only through the service process.

## Decisions taken during brainstorming

| Question | Answer |
|---|---|
| What is reachable remotely | Both the MCP endpoint and the dashboard |
| Trust boundary | Private only. No public URL, no public ingress |
| Private access method | Tailscale. Free plan covers six users with unlimited devices each |
| Host | AWS EC2 |
| Database | AWS RDS Postgres, for durability — a crashed instance must not lose history |
| Database reachability | Only from the instance's security group. Not publicly accessible |
| Transport security | Tailscale Serve, so the dashboard has a real certificate |
| Sizing | `t4g.small` instance, `db.t4g.micro` database, 20 GB gp3 with autoscaling |
| Infrastructure ownership | **A devops teammate**, not the repo owner. This design must therefore hand over a brief |
| Scope of v1 | Simplified. See "What v1 leaves out" |

### Why public exposure was rejected

Every public option ends with agent-props, or something in front of it, owning authentication —
an identity provider, token issuance for non-browser clients, and revocation. Phase 1 lists
"no auth, no multi-tenancy" as explicit non-goals, and `author` is attribution rather than
authentication by design. A private network keeps that true instead of quietly reversing it.

An AWS gateway was considered and is technically workable — API Gateway REST APIs gained response
streaming in November 2025, so the 29-second integration timeout that would have broken MCP's
`text/event-stream` responses no longer applies (HTTP APIs still cannot stream). It was rejected on
the auth cost, not on capability. A public ALB is also fine for streaming, but its built-in OIDC is
a browser redirect flow that an MCP client cannot complete, which would mean two authentication
mechanisms kept in step.

## Architecture

```
teammate's laptop (Tailscale client)
        │  https://<host>.<tailnet>.ts.net      (Tailscale Serve terminates TLS)
        ▼
EC2 t4g.small — security group with ZERO inbound rules
   ├── nginx container      :8080   dashboard assets + reverse proxy for /mcp
   └── service container    :8000   holds the only database credential
              │
              ▼  5432, inbound source = the EC2 security group
   RDS Postgres db.t4g.micro — private subnets, publicly_accessible = false
```

The instance is in a public subnet for **egress only** — container pulls and Tailscale's own
outbound connection. There is no inbound rule of any kind: no SSH, no 8080, no ICMP. Tailscale
does not need one, because it connects outbound and falls back to a DERP relay when direct
NAT traversal fails. A private subnet plus a NAT Gateway would be equivalent in security posture
and costs roughly $32/month more, so it is not used.

### One origin, and why it is not negotiable

The dashboard and `/mcp` must share an origin. A browser cannot reach the MCP streamable-HTTP
endpoint cross-origin: `mcp` 2.2.0 applies CORS only to its OAuth routes, so `OPTIONS /mcp`
answers 405, no response carries `access-control-allow-origin`, and nothing exposes the
`mcp-session-id` header that every request after the handshake has to echo. This was measured
during M9 and is recorded as finding F-16 in `DECISIONS.md`. The dev server solves it with a Vite
proxy; production needs the same shape from a real server.

### How the database privacy requirement is enforced

Three independent facts, each checkable:

1. RDS is created with `publicly_accessible = false` and in a subnet group of private subnets.
2. Its inbound rule names the **EC2 instance's security group** as the source, not a CIDR block.
   Nothing else in the VPC can open a connection, even from the same subnet.
3. Only the `service` container is given `AGENTPROPS_STORE`. nginx has no credential and no route.

`docker-compose.prod.yml` defines no database service and publishes no database port, which is the
repo-side half of the same guarantee. Note that today's `docker-compose.yml` **does** publish
Mongo on 27117 and Postgres on 5442 for local development; the production file is separate and
must not inherit those mappings.

## Components

### New, in this repo

| Artifact | Purpose |
|---|---|
| `web/Dockerfile` | Builds the dashboard (`npm ci && npm run build`) and serves `dist/` from nginx |
| `deploy/nginx.conf` | Static assets plus a reverse proxy for `/mcp` on one origin |
| `docker-compose.prod.yml` | `service` with no published port, `web` on 8080, no database |
| `docs/hosting.md` | Operator runbook: deploy, upgrade, roll back, back up, verify |
| `docs/devops-handoff.md` | **The brief for the teammate building the infrastructure** |

### Unchanged, and deliberately so

The service image already suits production: two-stage build against the committed lockfile,
non-root user at uid 10001, a TCP healthcheck rather than an HTTP one (the MCP endpoint answers
`/mcp` only for a POST carrying a handshake, so a `GET /` check would report a working server as
unhealthy), and the backend chosen entirely by `AGENTPROPS_STORE`.

Migrations need no new mechanism. The existing `shared` compose profile already runs
`alembic -x url="$AGENTPROPS_STORE" upgrade head` before exec'ing the server, because a Postgres
database is migrated and never created by the adapter. It is idempotent, so a restart costs one
query against `alembic_version`. Production points the same command at RDS.

### The nginx detail that will break everything if missed

`/mcp` must be proxied with `proxy_buffering off`, `proxy_http_version 1.1`, and a long
`proxy_read_timeout`, and `text/event-stream` must not be gzipped. A buffered SSE response does not
error — it hangs, and the dashboard looks broken with nothing in any log. This is the single most
likely way for a correct deployment to appear faulty.

## Data flow

1. A teammate's harness or agent opens `https://<host>.<tailnet>.ts.net/mcp` and handshakes.
   nginx proxies to the service container; the session id header round-trips unbuffered.
2. `run_start` pins a dataset, `fetch_step` serves fixtures, `record_step` stores actuals — all
   against RDS, through the service, as they already do against any Postgres.
3. A reviewer opens the same hostname in a browser, gets the dashboard from nginx, and every tool
   call it makes is same-origin.
4. Concurrent writers are already handled: writes are compare-and-set, dataset edits are
   copy-on-write, and each run pins its dataset version for its whole life.

## Error handling and failure modes

| Failure | Behaviour | Mitigation |
|---|---|---|
| Instance crashes or reboots | Containers restart (`restart: unless-stopped`); no data lost, since state is in RDS | None needed |
| RDS unavailable | Service fails its migration step and does not serve. Tools do not silently degrade | RDS automated backups; multi-AZ is a later upgrade |
| Tailscale key expires | Node drops off the tailnet; nothing is exposed, teammates simply lose access | Use an ephemeral-capable auth key from an OAuth client, and disable key expiry on the node |
| Nobody can reach the box | No SSH and no inbound rules by design | SSM Session Manager. See the pushback below |
| Storage fills | "Archive, never delete" means the store only grows | gp3 storage autoscaling on RDS |

## Testing

Two static guards, kept in v1 because they are nearly free and they encode the constraint that
motivated the whole design:

- `docker-compose.prod.yml` defines no database service and publishes no database port.
- `deploy/nginx.conf` disables proxy buffering on the `/mcp` location.

Both follow the existing pattern of deriving a claim from a real artifact rather than asserting it
in prose — the same reason `test_web_writes_through_tools.py` reads the running tool registry
instead of a hand-kept list.

## What v1 leaves out

Dropped for the first cut, at the owner's direction:

- **An end-to-end smoke test** bringing up the production stack against a throwaway Postgres and
  driving a tool call through nginx. Worth stating plainly: without it, the first proof that the
  same-origin path works is a teammate loading the dashboard by hand. Given that buffered SSE
  fails as a hang rather than an error, that is the failure this test would have caught. Recommend
  adding it once the deployment is up.
- **Terraform written in this repo.** How the infrastructure is codified is the devops teammate's
  call; the handoff brief states the required end state and the constraints, not the tooling.
- **Secrets Manager as a requirement.** Any mechanism that keeps the database password out of the
  repository is acceptable in v1 — SSM Parameter Store, Secrets Manager, or an instance-local env
  file written at provisioning time.
- **Multi-AZ RDS, read replicas, autoscaling, CI/CD.** Not warranted at this size.

### One argued exception

SSM Session Manager was in the list to drop, and this design keeps it. The reason is specific
rather than general caution: the instance has **no inbound rules at all**, so if Tailscale fails
to come up during provisioning — a bad auth key, an expired one, an egress rule that blocks
41641 and 443 — there is no way in. The recovery is to terminate the instance and rebuild it,
losing whatever was configured by hand. Attaching one AWS-managed policy to the instance role
costs a line of configuration and removes that whole class of outage.

This is a recommendation, not a veto. Say so and it comes out.

## Handoff brief: what the devops teammate needs

`docs/devops-handoff.md` will carry this. Summarised here so the design is self-contained.

**Required end state**

- One EC2 `t4g.small` (ARM), Amazon Linux 2023 or Ubuntu LTS, Docker and Compose installed,
  joined to the company tailnet, with Tailscale Serve fronting port 8080 over HTTPS.
- Its security group has **no inbound rules**. Egress open, or at minimum 443 plus Tailscale's
  UDP requirements.
- One RDS Postgres `db.t4g.micro`, 20 GB gp3 with storage autoscaling, automated backups on with
  a 7-day window, `publicly_accessible = false`, in a subnet group of two private subnets.
- The RDS security group admits 5432 **from the EC2 security group only**, by security-group id.
- An IAM instance profile with `AmazonSSMManagedInstanceCore`. Listed as required despite the
  v1 simplification — see "One argued exception" below.
- The database password stored outside the repository and delivered to the instance as an
  environment variable.

**What the application needs from the infrastructure — the whole contract**

- Exactly one environment variable: `AGENTPROPS_STORE`, a `postgresql://` URL pointing at RDS.
- Port 8080 reachable on the tailnet interface. Port 8000 must **not** be published.
- Outbound network access for container image pulls.

**Verification the teammate should run before handing it back**

1. From outside the tailnet, confirm the instance's public IP answers nothing on 22, 8080 or 8000.
2. From the instance, confirm a connection to RDS succeeds.
3. From another machine in the VPC but a different security group, confirm a connection to RDS is
   **refused** — this is the test of the requirement, and a CIDR-based rule would pass step 2
   while failing this one.
4. From a teammate's laptop on the tailnet, load the dashboard over HTTPS and confirm the agent
   list renders — which proves the same-origin `/mcp` path end to end.

**Explicitly out of scope for the teammate:** any authentication, TLS certificate management
beyond `tailscale serve`, and any public DNS record.

## Cost

Roughly $12/month for the instance and $13/month for the database on-demand, plus a few dollars of
storage and backups. Tailscale is free at six users or fewer. No NAT Gateway, no load balancer, no
public IPv4 charge if the instance is reached only over the tailnet.

## Open questions

1. Is the team six people or fewer? Above six, Tailscale moves to roughly $6–7 per user per month.
2. Who builds the dashboard image — CI, or `docker compose build` on the instance? Building on a
   `t4g.small` works but is slow; a registry is the better answer once there is a second deploy.
3. Should the existing local `docker-compose.yml` keep publishing database ports now that a
   production file exists? They are useful for local inspection and harmless on a laptop.
