import test from 'node:test'
import assert from 'node:assert/strict'

import { holymanUpdateCheckPath } from '../src/api/jargonUpdate.ts'

test('holymanUpdateCheckPath uses cached check by default and force query when requested', () => {
  assert.equal(holymanUpdateCheckPath(false), '/api/jargon/holyman/update/check')
  assert.equal(holymanUpdateCheckPath(true), '/api/jargon/holyman/update/check?force=true')
})
