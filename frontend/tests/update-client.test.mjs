import assert from 'node:assert/strict';
import test from 'node:test';
import { createAdminClient } from '../src/data/incidents.js';

test('reviewer update action sends explicit changes with authorization', async () => {
  const requests = [];
  const client = createAdminClient('local-test-token', {
    baseUrl: '/api',
    fetchImpl: async (url, options) => {
      requests.push({url, options});
      return {ok: true, json: async () => ({candidate: {status: 'approved'}, incident: {id: 'incident-one'}})};
    },
  });
  const payload = {
    reason: 'Confirmed by two reviewed sources',
    changes: {impact: 'Port delayed for one day'},
    sourceUrls: ['https://example.invalid/update'],
  };
  const updated = await client.applyUpdate('candidate/one', payload);
  assert.equal(updated.candidate.status, 'approved');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/admin/candidates/candidate%2Fone/apply-update');
  assert.equal(requests[0].options.method, 'POST');
  assert.equal(requests[0].options.headers.Authorization, 'Bearer local-test-token');
  assert.deepEqual(JSON.parse(requests[0].options.body), payload);
});

test('linked incident preview only makes a GET request', async () => {
  const requests = [];
  const client = createAdminClient('local-test-token', {
    baseUrl: '/api',
    fetchImpl: async (url, options) => {
      requests.push([url, options.method]);
      return {ok: true, json: async () => ({id: 'incident-one', impact: 'original'})};
    },
  });
  const incident = await client.incident('incident-one');
  assert.equal(incident.impact, 'original');
  assert.deepEqual(requests, [['/api/incidents/incident-one', 'GET']]);
});
