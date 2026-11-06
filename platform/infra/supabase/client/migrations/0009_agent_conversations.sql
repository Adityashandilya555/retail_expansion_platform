-- 0009: the admin agent's conversations (#38). Each message the model sees is one row, sent back
-- on every later call exactly as stored: Opus 5.5 checks that earlier turns, thinking blocks
-- included, were never edited. So both tables are insert-only, like 0005's draft_changes: zm_api
-- may only read and insert, and the triggers below refuse updates, deletes and truncate for
-- everyone else.
-- `content` is json, not jsonb: jsonb reorders object keys, and a tool call's input sent back in
-- another order is another prompt.

create table app.agent_conversations (
  id         uuid primary key default gen_random_uuid(),
  owner_id   uuid not null,                                         -- platform.owners.id
  mode       text not null check (mode in ('configure', 'edit')),  -- effort high / medium
  started_at timestamptz not null default now()
);

create table app.agent_messages (
  conversation_id uuid not null references app.agent_conversations (id),
  seq             int not null,
  role            text not null check (role in ('user', 'assistant', 'system')),
  content         json,   -- null: a call that stopped (refusal, max_tokens), kept for its usage only
  usage           jsonb,  -- an assistant row's usage, as the API reported it
  at              timestamptz not null default now(),
  primary key (conversation_id, seq)
);
revoke update, delete on app.agent_conversations, app.agent_messages from zm_api;

create function app.agent_insert_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'app.% is insert-only', tg_table_name;
end $$;

create trigger agent_conversations_insert_only before update or delete on app.agent_conversations
  for each row execute function app.agent_insert_only();
create trigger agent_messages_insert_only before update or delete on app.agent_messages
  for each row execute function app.agent_insert_only();
-- truncate fires no row triggers
create trigger agent_conversations_no_truncate before truncate on app.agent_conversations
  for each statement execute function app.agent_insert_only();
create trigger agent_messages_no_truncate before truncate on app.agent_messages
  for each statement execute function app.agent_insert_only();
