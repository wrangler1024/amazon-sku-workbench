import assert from 'node:assert/strict';
import test from 'node:test';
import {listingIssues,measurements,copyReviewedListing,matchingListingReview} from '../src/listingQuality.ts';
import {briefIssues,issueLabel} from '../src/briefPolicy.ts';
const identity={version:'source-identity-1',checks:[{code:'source_identifier',pattern:'(?<![A-Za-z0-9_.-])XK-108(?![A-Za-z0-9_.-])',message:'含来源型号或货号：XK-108'}]};
const policy={...identity,confirmed:true,facts:[{id:'dimensions',label:'产品尺寸与重量',text:'20 x 8 x 5 cm，0.3 kg'}],measurements:measurements('20 x 8 x 5 cm，0.3 kg').map(m=>({...m,fact:'dimensions'}))};
const listing={title:'Brackets for ZX200',highlights:'USB-C, M6, CR2032',bullets:['Dimensions: 20 x 8 x 5 cm','Weight: 0.3 kg'],description:'PET bracket',terms:'mount'};
test('checks track manual changes while retaining valid specifications and converted units',()=>{
 const value=structuredClone(listing);assert.deepEqual(listingIssues(value,policy),[]);
 value.highlights='XK-108';value.bullets[0]='Unformatted 50 cm.';
 const before=structuredClone(value),issues=listingIssues(value,policy);
 assert(issues.some(x=>x.code==='source_identifier'));assert(issues.some(x=>x.code==='unsupported_measurement'));assert(issues.some(x=>x.code==='missing_detail'));assert(issues.some(x=>x.code==='bullet_format'));assert(issues.some(x=>x.code==='bullet_ending'));
 assert.deepEqual(value,before);
 value.highlights='M6';value.bullets[0]='Dimensions: 200 x 80 x 50 mm';value.bullets[1]='Weight: 300 g';
 assert.deepEqual(listingIssues(value,policy),[]);
 value.bullets[1]='Weight: 0.301 kg';assert(listingIssues(value,policy).some(x=>x.code==='unsupported_measurement'));
 assert.equal(issueLabel('bullets.2'),'第 3 条五点');assert.equal(issueLabel('facts.dimensions'),'产品尺寸与重量');
});
test('model coverage is tied to the same unedited candidate and confirmed facts',()=>{
 const review={policy,issues:[{field:'facts.material',code:'coverage_unverified',message:'核对材质'}]};
 const jobs=[{task:'listing',result:{candidate:structuredClone(listing),listing_review:review}}];
 assert.equal(matchingListingReview(listing,jobs),review);
 assert.equal(matchingListingReview({...listing,description:'manually edited'},jobs),undefined);
 assert(listingIssues(listing,policy,review).some(x=>x.code==='coverage_unverified'));
 assert(!listingIssues(listing,{...policy,facts:[]},review).some(x=>x.code==='coverage_unverified'));
});
test('source identity guard covers listing fragments and artwork cards without changing copy text',async()=>{
 const writes=[];
 await assert.rejects(copyReviewedListing('XK-108',identity,async v=>writes.push(v)),/来源型号/);
 assert.equal(writes.length,0);
 const valid='USB-C, M6 and CR2032 for ZX200';await copyReviewedListing(valid,identity,async v=>writes.push(v));assert.deepEqual(writes,[valid]);
 assert(briefIssues({plans:[{text:'XK-108'}]},identity).length);
 await copyReviewedListing('SKU: OWN-XK-108-US',identity,async v=>writes.push(v));
});

test('operator notes stay in the review panel and never join copied sales text',async()=>{
 const note={field:'facts.dimensions',code:'operator_review',message:'尺寸归属需运营核实'};
 const review={policy,issues:[note]},writes=[];
 assert(listingIssues(listing,policy,review).includes(note));
 await copyReviewedListing(listing.description,policy,async text=>writes.push(text));
 assert.deepEqual(writes,[listing.description]);
 assert(!listingIssues(listing,{...policy,facts:[]},review).includes(note));
});
