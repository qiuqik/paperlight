import assert from 'node:assert/strict';
import test from 'node:test';

import {contextOffsets, findQuoteOffsets, normalizeQuote} from '../src/lib/quoteAnchor.ts';

test('finds the quote after text inserted before the saved offset', () => {
  const text = 'New introduction. We propose a latent diffusion framework that generates images.';
  const quote = 'latent diffusion framework';
  const match = findQuoteOffsets(text, quote, 'We propose a ', ' that generates');
  assert.equal(text.slice(match.start, match.end), quote);
  assert.equal(match.start, text.indexOf(quote));
});

test('uses context to distinguish repeated quotes', () => {
  const text = 'First result matters. Second result differs.';
  const match = findQuoteOffsets(text, 'result', 'Second ', ' differs');
  assert.equal(match.start, text.lastIndexOf('result'));
  assert.equal(findQuoteOffsets(text, 'result', '', ''), null);
});

test('orders context candidates near the saved position', () => {
  assert.deepEqual(contextOffsets('before x before y', 'before ', 'before', 15), [16, 7]);
  assert.equal(normalizeQuote(' two\n  words '), 'two words');
});
