-- 0010: an agent conversation started to apply a change request (#46, S2-24b) keeps a snapshot of
-- it: {id, kind, summary, payload, note}. The request lives in the control plane
-- (platform.change_requests); the snapshot lets each turn tell the agent about it, and every edit
-- the conversation makes is logged with its id (app.draft_changes.change_request_id). Written once,
-- on insert, like the rest of the row (0009's triggers still refuse updates). Null for an ordinary
-- conversation.
alter table app.agent_conversations add column change_request jsonb;
