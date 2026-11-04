-- 0006: invites (S2-23). zm_api gets read + write through 0001's default privileges in schema app.

-- one-time sign-up links: the token is `<client slug>.<random>`, and only the random part's sha256
-- is kept. `sealed` holds the whole token, Fernet-encrypted (SECRETS_ENCRYPTION_KEY), only until
-- the worker has emailed it; accepting deletes the row.
create table app.invites (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references app.users (id) on delete cascade,
  token_hash text not null unique,
  sealed     bytea,
  expires_at timestamptz not null,
  sent_at    timestamptz,
  created_at timestamptz not null default now()
);
