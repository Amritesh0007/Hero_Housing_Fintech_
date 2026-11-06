# 11 · Authentication and Security

## 1. Threat model (what we protect)

| Asset | Threat | Control |
|---|---|---|
| Valuation results | Tampering to inflate collateral | Append-only, hash-chained audit log; overrides need reason + role |
| API | Abuse, scraping, credential stuffing | Auth, rate limits, lockout, API key scopes |
| Model & data | Theft of licensed / lender data | RBAC, encrypted storage, no data in git |
| Lender data (pilot) | Personal data exposure | Minimise: valuation needs property, not borrower; DPDP-aligned handling |
| Webhooks | Spoofed callbacks | HMAC signatures, timestamp + replay window |
| MCP | Prompt-injected agent misuse | Read-mostly tools, scoped key, human confirms writes |

## 2. Authentication

**Users (dashboard):** OAuth2 password flow → JWT.
- Passwords hashed with **Argon2id** (`argon2-cffi`).
- Access token 15 min, refresh token 7 days, rotated on use; refresh tokens stored hashed so they can be revoked.
- Lockout / backoff after 5 failed attempts.
- P2: SSO with the lender's IdP (OIDC) instead of passwords.

**Systems (LOS, batch, MCP):** API keys.
- Format `vg_live_<prefix>_<secret>`; store only `sha256(secret)` + prefix.
- Shown once at creation; scoped; expiring; revocable.

```python
# api/deps.py (skeleton)
async def current_principal(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
) -> Principal:
    # TODO(you): if x_api_key -> look up by prefix, constant-time compare hash, check scopes/expiry
    #            elif bearer -> decode JWT (verify exp, iss, aud), load user, check is_active
    #            else -> 401
    ...
```
Use `hmac.compare_digest` for comparisons — learn why timing attacks matter.

## 3. Authorization (RBAC)

| Role | Can |
|---|---|
| `underwriter` | Create/read own valuations, send to review |
| `reviewer` | Read all, act on review queue, override with reason |
| `risk` | Read all, audit, model status, evaluation |
| `admin` | Users, keys, cities, sources, webhooks |
| `integration` | Scoped API-key access only |

Enforce with a dependency: `Depends(require_scope("valuations:write"))`. Check **object-level** access too (underwriter can't read another branch's valuation if policy says so).

## 4. Input & API hardening
- Pydantic models with strict types, ranges, `extra="forbid"`.
- Size limits: JSON body 64 KB; batch CSV 10 MB / 10k rows.
- SQLAlchemy parameters only — never f-string SQL.
- CORS: explicit allow-list from env.
- Security headers (HSTS, X-Content-Type-Options, CSP on dashboard).
- Disable `/docs` in production or put behind auth.
- Error responses never include stack traces.

## 5. Secrets & config
- `.env` locally, secrets manager in pilot. Nothing secret in images or git.
- `detect-private-key` pre-commit hook; GitHub secret scanning on.
- Rotate JWT secret and webhook secrets on a schedule.

## 6. Data protection
- TLS everywhere outside the local machine.
- Encryption at rest (managed DB / disk encryption).
- Least-privilege DB roles: `vg_app` (CRUD on app tables, INSERT-only on `audit_log`), `vg_readonly` (analysts, MCP dev).
- Backups encrypted and restore-tested.
- Logs: no tokens, keys or personal data — redact in the structlog processor.

## 7. Regulatory fit (verify before pilot)
- **RBI / NHB:** HFCs are regulated by RBI (with NHB supervision). Check the current Master Direction for HFCs on property valuation and LTV requirements; the engine is **decision support**, and the routing policy must keep physical valuation wherever regulation or HHF credit policy requires it.
- **Digital Personal Data Protection Act, 2023 (and Rules):** purpose limitation, minimisation, retention limits, breach handling. v1 stores no borrower PII.
- **Data licences:** each source's licence recorded in `data_source`; OSM attribution shown in UI/report.
- **Model governance:** versioned models, documented validation, override monitoring, periodic back-testing — mirrors common model-risk-management practice.

## 8. Security checklist (gate for M6)
- [ ] All endpoints except `/health`, `/auth/*` require auth (test enumerates routes).
- [ ] Scope tests: each role gets 403 where expected.
- [ ] `bandit` and `pip-audit` clean in CI.
- [ ] Rate limiting verified with a test.
- [ ] Audit log UPDATE/DELETE rejected at DB level.
- [ ] Webhook signature verified, replay window 5 min.
- [ ] Secrets absent from image (`docker history`, `trufflehog` scan).
