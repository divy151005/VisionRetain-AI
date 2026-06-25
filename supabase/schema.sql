create extension if not exists pgcrypto;

create table if not exists public.dashboard_metrics (
  key text primary key,
  label text not null,
  value numeric not null,
  delta text,
  color text,
  icon text,
  format text,
  sort_order integer not null default 0,
  updated_at timestamptz not null default now()
);

create table if not exists public.customers (
  id text primary key,
  name text not null,
  email text not null,
  plan text not null,
  spend numeric not null default 0,
  churn numeric not null default 0 check (churn between 0 and 100),
  risk text not null,
  segment text,
  ltv numeric not null default 0,
  nps integer,
  tenure integer not null default 0,
  last_active text,
  updated_at timestamptz not null default now()
);

create table if not exists public.product_scans (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid references auth.users(id) on delete set null,
  image_sha256 text not null,
  product_name text not null,
  brand text,
  model text,
  category text,
  confidence numeric(5,2) check (confidence between 0 and 100),
  identity_status text not null default 'category_only'
    check (identity_status in ('exact', 'probable', 'category_only', 'unknown')),
  objects jsonb not null default '[]'::jsonb,
  features jsonb not null default '[]'::jsonb,
  ocr_text text,
  price_min numeric,
  price_max numeric,
  currency text,
  prices_fetched_at timestamptz,
  provider_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.price_listings (
  id uuid primary key default gen_random_uuid(),
  scan_id uuid not null references public.product_scans(id) on delete cascade,
  owner_id uuid references auth.users(id) on delete set null,
  store text not null,
  title text not null,
  price numeric not null check (price >= 0),
  currency text not null,
  product_url text not null,
  source_url text,
  availability text,
  rating numeric,
  relevance numeric(5,4),
  fetched_at timestamptz not null default now()
);

create index if not exists product_scans_owner_created_idx
  on public.product_scans(owner_id, created_at desc);
create index if not exists price_listings_scan_idx on public.price_listings(scan_id);
create index if not exists price_listings_title_idx on public.price_listings using gin(to_tsvector('simple', title));

alter table public.product_scans enable row level security;
alter table public.price_listings enable row level security;
alter table public.dashboard_metrics enable row level security;
alter table public.customers enable row level security;

create policy "Users can read their product scans"
  on public.product_scans for select
  using (auth.uid() = owner_id);
create policy "Users can read their price listings"
  on public.price_listings for select
  using (auth.uid() = owner_id);

insert into public.dashboard_metrics (key, label, value, delta, color, icon, format, sort_order)
values
  ('total_customers', 'Total Customers', 84291, '+12.4%', '#00E5FF', '◉', null, 1),
  ('monthly_revenue', 'Monthly Revenue', 2847000, '+8.7%', '#00FF88', '◇', 'money', 2),
  ('revenue_at_risk', 'Revenue at Risk', 342000, '-3.2%', '#FF5C5C', '△', 'money', 3),
  ('high_risk_customers', 'High-Risk Customers', 2841, '+5.1%', '#FFC857', '▲', null, 4)
on conflict (key) do nothing;

insert into public.customers
  (id, name, email, plan, spend, churn, risk, segment, ltv, nps, tenure, last_active)
values
  ('C001', 'Arjun Mehta', 'arjun@techcorp.in', 'Enterprise', 48200, 87, 'Critical', 'Enterprise', 578400, 22, 14, '32 days ago'),
  ('C002', 'Priya Sharma', 'priya@startup.io', 'Pro', 12400, 23, 'Low', 'SMB', 148800, 71, 8, '2 days ago'),
  ('C003', 'Rahul Gupta', 'rahul@ecom.com', 'Business', 28900, 61, 'High', 'B2B', 346800, 38, 22, '11 days ago'),
  ('C004', 'Sneha Patel', 'sneha@retail.in', 'Starter', 4200, 44, 'Medium', 'SMB', 50400, 55, 5, '5 days ago'),
  ('C005', 'Vikram Singh', 'vikram@mfg.co', 'Enterprise', 91500, 12, 'Low', 'Enterprise', 1098000, 84, 36, '1 day ago'),
  ('C006', 'Ananya Roy', 'ananya@fin.tech', 'Pro', 19800, 78, 'High', 'B2B', 237600, 29, 11, '19 days ago'),
  ('C007', 'Karan Verma', 'karan@media.in', 'Business', 33100, 95, 'Critical', 'B2B', 397200, 11, 7, '45 days ago'),
  ('C008', 'Deepika Nair', 'deepika@health.io', 'Starter', 6700, 31, 'Low', 'SMB', 80400, 67, 10, '3 days ago')
on conflict (id) do nothing;
