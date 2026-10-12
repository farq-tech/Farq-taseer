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
