-- Reproducible Haraj corpus audit against farq-main construction.suppliers.
-- Read only. Do not treat source_ref ad annotations as stored ads.

-- Grain and identity
select count(*) as raw_rows,
       count(distinct external_key) as unique_sellers,
       count(distinct name_ar) as unique_names
from construction.suppliers
where source_system = 'HARAJ';

-- Annotated ad counts are labels, not ad rows.
select count(*) filter (where source_ref ~ '^haraj:[0-9]+ad$') as parsed_refs,
       count(*) filter (where source_ref !~ '^haraj:[0-9]+ad$') as unparsed_refs,
       sum((substring(source_ref from 'haraj:([0-9]+)ad'))::bigint) as annotated_ad_count_sum
from construction.suppliers
where source_system = 'HARAJ';

-- Coverage
select count(*) filter (where city is null or btrim(city) = '') as missing_city,
       count(*) filter (where district is null or btrim(district) = '') as missing_district,
       count(*) filter (where directory_metadata is not null) as with_metadata,
       count(*) filter (where active) as active_rows,
       min(created_at) as oldest_ingest,
       max(created_at) as newest_ingest
from construction.suppliers
where source_system = 'HARAJ';

-- Other sources that must not be mixed into consumer results
select source_system::text, count(*)
from construction.suppliers
group by 1
order by 2 desc;
