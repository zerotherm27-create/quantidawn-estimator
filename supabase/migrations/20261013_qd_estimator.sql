-- QuantiDawn estimator, for a Supabase project that is SHARED with the QuantiDawn website.
-- Everything here is prefixed qd_ / qd-estimator so it sits beside the website's tables and never touches them.
-- Safe to run more than once.

-- Small JSON documents: projects, saved work, client download links, team members.
create table if not exists public.qd_docs (
  coll       text        not null,
  id         text        not null,
  data       jsonb       not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (coll, id)
);
create index if not exists qd_docs_share_project on public.qd_docs ((data ->> 'project')) where coll = 'share';

-- Row level security ON with no policies: the public (browser) keys cannot read or write this table at all.
-- Only the estimator server, using the secret service key, can.
alter table public.qd_docs enable row level security;

-- Atomic counter used for "limit N downloads" on client links.
create or replace function public.qd_bump(p_coll text, p_id text, p_field text, p_limit int)
returns int
language plpgsql
security invoker
set search_path = public
as $$
declare v int;
begin
  update public.qd_docs
     set data = jsonb_set(data, array[p_field], to_jsonb(coalesce((data ->> p_field)::int, 0) + 1)),
         updated_at = now()
   where coll = p_coll and id = p_id
     and (p_limit <= 0 or coalesce((data ->> p_field)::int, 0) < p_limit)
  returning (data ->> p_field)::int into v;
  return v;
end $$;
revoke all on function public.qd_bump(text, text, text, int) from public, anon, authenticated;
grant execute on function public.qd_bump(text, text, text, int) to service_role;

-- Private file bucket for uploaded plans, rendered pages, templates and shared workbooks.
-- No storage policies are added, so only the server's service key can use it.
insert into storage.buckets (id, name, public)
values ('qd-estimator', 'qd-estimator', false)
on conflict (id) do nothing;
