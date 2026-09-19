import { describe, expect, it } from 'vitest'
import {
  LINUXDO_CALLBACK_PATH,
  linuxdoCallbackUrl,
  mergeEnabledProfileIds,
  moveOrderedId,
  syncRegisterProbeProfileRounds,
} from './settings-model'

describe('moveOrderedId', () => {
  it('moves a selected profile up and down without dropping others', () => {
    expect(moveOrderedId(['a', 'b', 'c'], 'c', -1)).toEqual(['a', 'c', 'b'])
    expect(moveOrderedId(['a', 'c', 'b'], 'c', -1)).toEqual(['c', 'a', 'b'])
    expect(moveOrderedId(['c', 'a', 'b'], 'c', -1)).toEqual(['c', 'a', 'b'])
    expect(moveOrderedId(['c', 'a', 'b'], 'c', 1)).toEqual(['a', 'c', 'b'])
  })
})

describe('mergeEnabledProfileIds', () => {
  it('keeps the current selection order and appends remaining enabled profiles', () => {
    expect(mergeEnabledProfileIds(['c', 'a'], ['a', 'b', 'c'])).toEqual([
      'c',
      'a',
      'b',
    ])
  })
})

describe('linuxdoCallbackUrl', () => {
  it('builds the public Linux DO callback from the current origin', () => {
    expect(linuxdoCallbackUrl()).toBe(
      new URL(LINUXDO_CALLBACK_PATH, window.location.origin).toString()
    )
  })
})

describe('syncRegisterProbeProfileRounds', () => {
  it('keeps rounds aligned to the selected profile order', () => {
    expect(
      syncRegisterProbeProfileRounds(
        ['b', 'a'],
        { a: 4, b: 1, extra: 9 },
        3
      )
    ).toEqual({ b: 1, a: 4 })
  })
})
