"""Exercise the browser selection policy without contacting any provider."""
from pathlib import Path
import subprocess


def test_connected_selection_crosses_six_and_legacy_keeps_its_cap():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    start = source.index('function sellerCap() {')
    end = source.index('\n}', start) + 2
    script = '''const assert = require('node:assert/strict');
let data = {sellers_per_item:20, seller_selection_unlimited:false};
const allowance = () => data; const TRIAL_SELLERS = 6;
''' + source[start:end] + '''
assert.equal(sellerCap(), 20);
assert.equal(20 >= sellerCap(), true);
assert.equal(7 >= sellerCap(), false);
data.sellers_per_item = 6;
assert.equal(sellerCap(), 6);
assert.equal(6 >= sellerCap(), true);
'''
    subprocess.run(['node', '-e', script], check=True)


def test_listing_price_disclosure_preserves_evidence_without_turning_uncertain_amounts_into_prices():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    functions = []
    for signature in (
        'function unescapeOnce(text) {',
        'function adOriginalText(text) {',
        'function harajListingUrl(ad) {',
        'function adPriceDisclosure(ad, showMissing = false) {',
    ):
        start = source.index(signature)
        end = source.index('\n}', start) + 2
        functions.append(source[start:end])
    script = '''const assert = require('node:assert/strict');
const money = (value) => `${value} ر.س`;
const esc = (value) => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
''' + '\n'.join(functions) + '''
let html = adPriceDisclosure({price_evidence:{kind:'asking_price_in_field',amount:2000,source:'price_field'}});
assert.match(html, /السعر المذكور في خانة الإعلان: 2000 ر\\.س/);
assert.match(html, /ليس عرضًا نهائيًا على طلبك/);
html = adPriceDisclosure({price_evidence:{kind:'explicit_text_price',amount:1200,source:'description'}});
assert.match(html, /سعر مذكور في الوصف: 1200 ر\\.س/);
html = adPriceDisclosure({price_evidence:{kind:'estimated_price',amount:900,source:'description',estimated:true}});
assert.match(html, /السعر التقديري المذكور: 900 ر\\.س/);
assert.match(html, /ليس سعرًا مؤكدًا/);
html = adPriceDisclosure({price_text:'900 - 1200',price_evidence:{kind:'price_range',source:'price_field'}});
assert.match(html, /نطاق السعر في الإعلان: 900 - 1200/);
assert.doesNotMatch(html, /900 ر\\.س/);
html = adPriceDisclosure({price_evidence:{kind:'price_range',source:'description',text:'من 800 حتى 200'}});
assert.match(html, /نطاق السعر المذكور في الوصف: من 800 حتى 200/);
assert.match(html, /لا نعامل النطاق كسعر واحد للمقارنة/);
html = adPriceDisclosure({price_text:'<img>',price_evidence:{kind:'ambiguous_amount',source:'price_field'}});
assert.match(html, /&lt;img&gt;/);
assert.doesNotMatch(html, /ر\\.س/);
html = adPriceDisclosure({price_evidence:{kind:'ambiguous_amount',source:'price_field',text_source:'description',text:'سعر الشراء 2700 ؛ سعر البيع 1200'}});
assert.match(html, /مبالغ مختلفة مذكورة في الوصف: سعر الشراء 2700 ؛ سعر البيع 1200/);
assert.match(html, /لم نعتبرها سعرًا نهائيًا واحدًا/);
assert.doesNotMatch(html, /2700 ر\\.س|1200 ر\\.س/);
assert.match(adPriceDisclosure({price_evidence:{kind:'auction_without_price'}}), /على السوم/);
assert.match(adPriceDisclosure({price_evidence:{kind:'contact_for_price'}}), /عند التواصل/);
assert.equal(adPriceDisclosure({price_evidence:{kind:'no_price_signal'}}), '');
assert.match(adPriceDisclosure({price_evidence:{kind:'no_price_signal'}}, true), /لم يذكر الإعلان سعرًا واضحًا/);
assert.equal(harajListingUrl({url:'https://haraj.com.sa/123'}), 'https://haraj.com.sa/123');
assert.equal(harajListingUrl({url:'javascript:alert(1)'}), '');
assert.equal(adOriginalText('عربة أطفال يويو\\nالسعر: 700 ريال\\nللتواصل واتس'), 'عربة أطفال يويو\\nالسعر: 700 ريال\\nللتواصل واتس');
'''
    subprocess.run(['node', '-e', script], check=True)


def test_search_cards_and_details_show_the_ad_price_disclosure_and_original_listing_link():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    assert 'const priceDisclosure = adPriceDisclosure(result.ad);' in source
    assert 'const priceDisclosure = adPriceDisclosure(result.ad, true);' in source
    assert 'const adTitle = adOriginalText(result.ad?.title);' in source
    assert 'const adDescription = adOriginalText(result.ad?.description);' in source
    assert 'class="fq-ad-original"' in source
    assert 'عرض الإعلان الأصلي في حراج' in source
    assert 'ليس عرضًا نهائيًا على طلبك' in source


def test_result_card_makes_the_complete_ad_details_obvious():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    start = source.index('function renderCard(result) {')
    end = source.index('\n}', start) + 2
    script = '''const assert = require('node:assert/strict');
const state = {seenCards:new Set(), selected:new Set()};
let newCardsInBatch = 0;
const resultKey = () => 'ad-1';
const sellerOf = () => ({id:'seller-1', name:'بائع'});
const displayName = () => ({name:'بائع', handle:''});
const needOf = () => '';
const multiNeed = () => false;
const cityLabel = (value) => value || '';
const snip = () => 'وصف الإعلان';
const adOriginalText = (value) => String(value || '').trim();
const matchedWords = () => [];
const adPriceDisclosure = () => '';
const offerMarks = () => [];
const adAge = () => null;
const imageSources = (ad) => ad?.image_ref ? ['https://cdn.test/400.webp'] : [];
const imageAvailabilityDisclosure = (ad) => ad?.source_has_image === false ? 'لا توجد صور مرفقة بإعلان حراج.' : ad?.source_has_image === true ? 'حراج يذكر وجود صور؛ افتح التفاصيل لمحاولة عرضها.' : '';
const esc = (value) => String(value);
const ic = () => '';
const withoutPrices = (value) => value;
''' + source[start:end] + '''
const html = renderCard({ad:{title:'عربية أطفال يويو', city:'الرياض', image_ref:'photo.jpg'}, seller:{id:'seller-1'}});
assert.match(html, /عرض تفاصيل الإعلان/);
assert.match(html, /data-action="open"/);
assert.doesNotMatch(html, /لا توجد صور/);
assert.match(html, /class="fq-shot" data-frame/);
const noImage = renderCard({ad:{title:'عربية أطفال يويو', source_has_image:false}, seller:{id:'seller-1'}});
assert.match(noImage, /لا توجد صور مرفقة بإعلان حراج/);
const missingThumbnail = renderCard({ad:{title:'عربية أطفال يويو', source_has_image:true}, seller:{id:'seller-1'}});
assert.match(missingThumbnail, /حراج يذكر وجود صور/);
const longDescription = 'وصف كامل كما كتبه البائع. '.repeat(5);
const detailed = renderCard({ad:{title:'عربية أطفال يويو', description:longDescription}, seller:{id:'seller-1'}});
assert.match(detailed, /عرض الوصف الكامل كما كتبه البائع/);
assert.ok(detailed.includes(longDescription.trimEnd()));
'''
    subprocess.run(['node', '-e', script], check=True)


def test_unavailable_listing_details_can_be_retried_from_the_detail_sheet():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    start = source.index('function detailSheet() {')
    end = source.index('\n}', start) + 2
    script = '''const assert = require('node:assert/strict');
const result = {ad:{url:'https://haraj.com.sa/123/post/', title:'عنوان الإعلان', listing_details_state:'unavailable'}, seller:{id:'seller-1', name:'بائع'}};
const state = {active:result, gallery:[], selected:new Set()};
const sellerOf = () => result.seller;
const displayName = () => ({name:'بائع', handle:''});
const adAge = () => null;
const imageSources = () => [];
const imageAvailabilityDisclosure = () => '';
const resultKey = () => 'seller-1:123';
const adOriginalText = (value) => String(value || '').trim();
const adPriceDisclosure = () => '';
const harajListingUrl = (ad) => ad?.url || '';
const place = () => '';
const cityLabel = () => '';
const initial = () => 'ب';
const esc = (value) => String(value);
''' + source[start:end] + '''
let html = detailSheet();
assert.match(html, /تعذر جلب تفاصيل الإعلان الأصلية الآن/);
assert.match(html, /data-action="retry-listing-details" data-key="seller-1:123"/);
assert.match(html, /أعد جلب تفاصيل الإعلان/);
result.ad.listing_details_state = 'loading';
html = detailSheet();
assert.doesNotMatch(html, /data-action="retry-listing-details"/);
result.ad.listing_details_state = 'unavailable';
result.ad.url = '';
html = detailSheet();
assert.doesNotMatch(html, /data-action="retry-listing-details"/);
'''
    subprocess.run(['node', '-e', script], check=True)
    assert 'else if (action === "retry-listing-details") openResult(target.dataset.key);' in source


def test_failed_listing_image_urls_show_an_explicit_fallback():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    start = source.index('function bindImages(root) {')
    end = source.index('\n}', start) + 2
    script = '''const assert = require('node:assert/strict');
''' + source[start:end] + '''
const classes = new Set();
const attributes = new Map([['aria-label', 'افتح إعلان عربية أطفال يويو']]);
const frame = {
  classList:{add:(name) => classes.add(name)},
  getAttribute:(name) => attributes.get(name) || null,
  setAttribute:(name,value) => attributes.set(name,value),
};
const listeners = {};
const removed = [];
const assigned = [];
const img = {
  dataset:{src:'https://cdn.test/400.webp|https://cdn.test/140.webp'},
  addEventListener:(name,fn) => {listeners[name] = fn;},
  closest:(selector) => selector === '[data-frame]' ? frame : null,
  removeAttribute:(name) => removed.push(name),
  set src(value) { assigned.push(value); },
};
bindImages({querySelectorAll:() => [img]});
assert.equal(assigned.length, 1);
listeners.error();
assert.equal(assigned.length, 2);
listeners.error();
assert.deepEqual(removed, ['src']);
assert.ok(classes.has('is-missing'));
assert.equal(attributes.get('aria-label'), 'افتح إعلان عربية أطفال يويو · تعذر تحميل الصورة');
'''
    subprocess.run(['node', '-e', script], check=True)


def test_search_results_can_continue_and_keep_previous_cards_when_a_page_fails():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    assert 'data-action="load-more"' in source
    assert 'تابع البحث عن إعلانات أكثر' in source
    assert 'هذه دفعة من النتائج، وقد توجد إعلانات أخرى في الصفحات التالية.' in source
    assert source.index('data-action="load-more"') < source.index('shown.map(renderCard).join("")')
    assert 'body: JSON.stringify({ query: asked, page, continuation_trace: traceId })' in source
    assert 'fetch("/v1/search/stream", {' in source
    assert 'const added = addResults(event.results);' in source
    assert 'توقف تحميل الإعلانات الإضافية قبل اكتماله.' in source
    assert 'state.nextPage = event.next_page ?? null;' in source
    assert 'state.searchId !== searchId || state.traceId !== traceId' in source
    assert '// Keep the page cursor and current cards so the customer can safely retry.' in source


def test_a_cleanly_ended_search_stream_uses_the_plain_search_recovery_once():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    assert 'await recoverSearch(searchId, asked, started, new Error("stream closed before completion"), controller.signal.aborted);' in source
    assert 'await recoverSearch(searchId, asked, started, _error, controller.signal.aborted);' in source
    assert 'async function recoverSearch(searchId, asked, started, error, timedOut) {' in source


def test_more_results_control_is_prominent_only_while_another_page_is_available():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    start = source.index('function renderFlow() {')
    end = source.index('\n}', start) + 2
    script = '''const assert = require('node:assert/strict');
const state = {searchState:'DONE', searching:false, partial:false, results:[{}], needFilter:'', selected:new Set(), nextPage:4, loadingMore:false, notice:'', view:'results', capSheet:false};
let newCardsInBatch = 0;
const fqHead = () => '';
const renderQuestion = () => '';
const renderSearching = () => '';
const multiNeed = () => false;
const needOf = () => '';
const visibleResults = () => state.results;
const uniqueSellerCount = () => 1;
const filtersRow = () => '<div>count</div>';
const esc = (value) => String(value);
const finalNotice = () => '';
const emptyState = () => '';
const renderCard = () => '<article data-card>listing</article>';
const resultKey = () => 'ad-1';
const formatCount = (value) => String(value);
const suppliers = (value) => `${value} suppliers`;
''' + source[start:end] + '''
let html = renderFlow();
assert.match(html, /قد توجد إعلانات أخرى في الصفحات التالية/);
assert.ok(html.indexOf('data-action="load-more"') < html.indexOf('data-card'));
state.loadingMore = true;
html = renderFlow();
assert.match(html, /disabled aria-busy="true"/);
state.nextPage = null;
html = renderFlow();
assert.doesNotMatch(html, /data-action="load-more"|قد توجد إعلانات أخرى/);
'''
    subprocess.run(['node', '-e', script], check=True)


def test_result_count_distinguishes_listings_from_unique_sellers():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    functions = []
    for signature in (
        'function formatCount(value) {',
        'function plural(count, [one, two, few, many]) {',
        'function foundLine(count) {',
        'function sellerOf(result) {',
        'function uniqueSellerCount(results) {',
        'function resultsLine(count, sellers) {',
    ):
        start = source.index(signature)
        end = source.index('\n}', start) + 2
        functions.append(source[start:end])
    script = '''const assert = require('node:assert/strict');
''' + '\n'.join(functions) + '''
const listings = Array.from({length: 13}, (_, index) => ({seller: {id: index < 2 ? 'seller-1' : `seller-${index}`}}));
assert.equal(uniqueSellerCount(listings), 12);
assert.equal(resultsLine(listings.length, uniqueSellerCount(listings)), 'لقينا 13 إعلانًا من 12 بائعًا');
assert.equal(resultsLine(2, 2), 'لقينا إعلانين من بائعين');
assert.equal(resultsLine(1, 1), 'لقينا إعلان واحد من بائع واحد');
assert.equal(resultsLine(13, uniqueSellerCount([{seller: {}}])), 'لقينا 13 إعلانًا');
'''
    subprocess.run(['node', '-e', script], check=True)

def test_opening_a_result_refreshes_it_from_the_original_haraj_listing():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    start = source.index('async function openResult(key) {')
    end = source.index('\n}', start) + 2
    script = r"""
const assert = require('node:assert/strict');
const result = {
  ad: {url: 'https://haraj.com.sa/1/post/', title: 'نتيجة مختصرة', description: 'وصف مقتطع', image_urls: ['thumb']},
};
const fallback = {
  ad: {url: 'https://haraj.com.sa/2/post/', title: 'عنوان البحث', description: 'الوصف الموجود في البحث', image_urls: ['thumb-2']},
};
const state = {results: [result, fallback], active: null, gallery: [], view: 'results'};
const calls = [];
let renders = 0;
const api = async (path) => {
  calls.push(path);
  if (path.startsWith('/v1/listings/images?')) {
    return {images: decodeURIComponent(path).includes('/1/post/') ? ['image-route-one'] : ['image-route-two']};
  }
  if (decodeURIComponent(path).includes('/1/post/')) return {
    state: 'available', title: 'عنوان الإعلان الأصلي', description: 'الوصف الأصلي كاملًا',
    city: 'الرياض', posted_at: '2026-10-15T00:00:00Z', price_amount: 300,
    price_currency: 'SAR', price_text: '300',
    price_evidence: {kind: 'asking_price_in_field', amount: 300, source: 'price_field'},
    images: ['haraj-photo-b', 'haraj-photo-a'],
  };
  return {state: 'unavailable'};
};
const render = () => { renders += 1; };
const resultKey = (item) => item === result ? 'first' : 'second';
const imageSources = (ad) => ad?.image_urls || [];
""" + source[start:end] + r"""
(async () => {
  await openResult('first');
  assert.equal(state.active, result);
  assert.equal(result.ad.title, 'عنوان الإعلان الأصلي');
  assert.equal(result.ad.description, 'الوصف الأصلي كاملًا');
  assert.equal(result.ad.price_evidence.amount, 300);
  assert.equal(result.ad.listing_details_state, 'available');
  assert.deepEqual(state.gallery, ['image-route-one', 'haraj-photo-b', 'haraj-photo-a']);

  await openResult('second');
  assert.equal(fallback.ad.title, 'عنوان البحث');
  assert.equal(fallback.ad.description, 'الوصف الموجود في البحث');
  assert.equal(fallback.ad.listing_details_state, 'unavailable');
  assert.deepEqual(state.gallery, ['image-route-two', 'thumb-2']);
  assert.equal(calls.length, 4);
  assert.equal(calls.filter((path) => path.startsWith('/v1/listings/details?url=')).length, 2);
  assert.equal(calls.filter((path) => path.startsWith('/v1/listings/images?url=')).length, 2);
  assert.ok(renders >= 4);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(['node', '-e', script], check=True)
    assert 'تعذر جلب تفاصيل الإعلان الأصلية الآن؛ نعرض البيانات التي وصلت من البحث.' in source
