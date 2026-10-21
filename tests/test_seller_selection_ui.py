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


def test_search_results_can_continue_and_keep_previous_cards_when_a_page_fails():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    assert 'data-action="load-more"' in source
    assert 'حمّل إعلانات أكثر' in source
    assert 'json: { query: asked, page, continuation_trace: traceId }' in source
    assert 'state.nextPage = event.next_page ?? null;' in source
    assert 'state.searchId !== searchId || state.traceId !== traceId' in source
    assert '// Keep the page cursor and current cards so the customer can safely retry.' in source
