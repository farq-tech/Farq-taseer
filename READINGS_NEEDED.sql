-- Set C readings: production's model reading of each query, from the shared understand cache.
-- key = 'understand:' || left(sha256(normalize(query || ' الرياض')), 40). Project mpgbvtaguerncgbzvpwg.
-- Paste each row's value (a JSON list with one need) into tests/fixtures/matching_live_prod_2026-09-26_c.json -> readings[query].
select key, value, updated_at
from taseer.haraj_channel
where key in (
  -- أبي طباخ للمناسبات
  'understand:2499c10518b4c182ca838a934ad554d059744013',
  -- أبي مصور أفراح
  'understand:b45f3b382597c71203a1f75652b969f37a2b428a',
  -- أبي سيارة أجرة توصيل مطار
  'understand:799316f847e844d73ec18ef317791dc20c7c54ec',
  -- أبي مدرس خصوصي رياضيات
  'understand:e0e38c3df592860858fa6ad80baeb9f49a58cc5b',
  -- أبي جهاز رياضي مشي
  'understand:0acfda89ff060c59728236a5f8b14c82bd087d11',
  -- أبي كاميرا كانون
  'understand:3fbff19993b132a50dcb134590d6d8880101753e',
  -- أبي جوال سامسونج S23
  'understand:c752acee3ec761a364cb1011155c53014440a91d',
  -- أبي أرض في الرياض شمال
  'understand:c0d703ce73a95bdef9584bae17bfb29653541c7e',
  -- أبي فيلا للبيع
  'understand:93b95c4d9b75505fd59286d5fe2ced2da9dd913b',
  -- أبي دينا نقل من الرياض للدمام
  'understand:9b3e622c2bf25e7142549ffa27387721f47d10ea',
  -- أبي خادمة نقل كفالة
  'understand:11f3b04fcc4a5d4ea0dc351015c23724e6c83a02',
  -- أبي صيانة مكيف شباك
  'understand:2a92ba321d5a1d3a16c84c2c0139520d7332f373',
  -- أبي تصميم ديكور
  'understand:4e59064ae85a827e95858255fdd4edac3917ad47',
  -- أبي عزل فوم سطح 200 متر
  'understand:024da00a4dc1151e8387b6d36437bf6ce4719136',
  -- أبي طباعة بروشورات 1000 نسخة
  'understand:482fc61e82c3163b7b1b410b5a5331a301e6712e',
  -- أبي ذبيحة تيس
  'understand:0ca42e4d04d781e7b1cf6272d309f7d0389b992b',
  -- أبي كرسي مكتب
  'understand:87bbe1338f7092c4a3347f8449efda4fa9a53710',
  -- أبي ثلاجة صغيرة للمكتب
  'understand:9ef59ff6a64f34516a342fa12b9fed52efdb8e79',
  -- أبي مكينة قهوة
  'understand:3c57c822c0410a42c642d5cb91dedc7ddedb8f77',
  -- أبي هدايا تخرج
  'understand:7b14c1c3c3830e6845fce27f54ca66d1c3073f06'
)
order by updated_at desc;

-- Rows that turn scored once their query's reading is present:
--   أبي مصور أفراح #2 [EXACT] تصوير حفلات
--   أبي مصور أفراح #3 [EXACT] مصور زواجات وحفلات ومؤتمرات في الرياض وضواحيها
--   أبي مصور أفراح #6 [EXACT] مصور حفلات
--   أبي مصور أفراح #7 [EXACT] اطلق مصور حفلات بالرياض
--   أبي مصور أفراح #9 [EXACT] تصوير حفلات ومناسبات
--   أبي سيارة أجرة توصيل مطار #4 [EXACT] توصيل مشاوير من والي المطار بالرياض
--   أبي مدرس خصوصي رياضيات #1 [EXACT] معلم رياضيات وقدرات كمي وتحصيلي   شرح مبسّط وواضح   تأ
--   أبي مدرس خصوصي رياضيات #2 [EXACT] معلم رياضيات مصري
--   أبي مدرس خصوصي رياضيات #3 [EXACT] معلم رياضيات وقدرات كمي لجميع أحياء الرياض
--   أبي مدرس خصوصي رياضيات #7 [EXACT] معلم رياضيات شمال وشرق الرياض
--   أبي مدرس خصوصي رياضيات #12 [EXACT] مستر محمود الشرقاوي معلم رياضيات وقدرات كمي وتأسيس ومتابعه
--   أبي جهاز رياضي مشي #4 [EXACT] أجهزة سير كهربائية رياضية ( مشي - جري )
--   أبي ذبيحة تيس #10 [EXACT] ذبايح ذبيحه نعيم هرفي جبر نعيمي وسط حري حريات لباني تيوس
--   أبي ذبيحة تيس #11 [EXACT] تيوس بلدية مع الذبح والتوصيل غرب الرياض
--   أبي ذبيحة تيس #12 [EXACT] تيوس مندي ذبح
--   أبي هدايا تخرج #8 [EXACT] بكس الهبة للخريجين هدية تخرج فخمة مع سلسال  نضاره
--   أبي هدايا تخرج #10 [EXACT] هديه تخرج
