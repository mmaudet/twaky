// This test file requires Vitest setup from T15.
import { describe, it, expectTypeOf } from 'vitest'
import type { paths, components } from './api-types'

describe('api-types.d.ts', () => {
    it('exposes the /missions path', () => {
        expectTypeOf<paths['/missions']>().toBeObject()
    })

    it('exposes the Mission schema', () => {
        expectTypeOf<components['schemas']['Mission']>().toBeObject()
    })

    it('exposes the MissionState enum', () => {
        // MissionState is a string enum; the type should include "declared"
        type S = components['schemas']['MissionState']
        expectTypeOf<'declared'>().toMatchTypeOf<S>()
        expectTypeOf<'awaiting_user'>().toMatchTypeOf<S>()
        expectTypeOf<'done'>().toMatchTypeOf<S>()
    })
})
