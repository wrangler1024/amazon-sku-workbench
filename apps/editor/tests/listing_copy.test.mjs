import assert from 'node:assert/strict';
import test from 'node:test';
import {descriptionHtml} from '../src/listingCopy.ts';

test('description HTML preserves paragraphs, dimensions and Unicode without active markup',()=>{
 assert.equal(descriptionHtml('Product overview\r\nSoft & light\r\n\r\nSpecifications\rLength < 20 cm; 6"; buyer\'s; café 中文'),
  'Product overview<br/>Soft &amp; light<br/><br/>Specifications<br/>Length &lt; 20 cm; 6&quot;; buyer&#39;s; café 中文');
 assert.equal(descriptionHtml('<script>alert(1)</script>\n<img src=x onerror=alert(1)>'),
  '&lt;script&gt;alert(1)&lt;/script&gt;<br/>&lt;img src=x onerror=alert(1)&gt;');
});

test('existing plain break tags normalize, while tags with attributes remain text',()=>{
 assert.equal(descriptionHtml('A<br>B<BR/>C<br />D</br>E'),'A<br/>B<br/>C<br/>D<br/>E');
 assert.equal(descriptionHtml('<br onclick="x">'),'&lt;br onclick=&quot;x&quot;&gt;');
 assert.equal(descriptionHtml(''),'');
});
