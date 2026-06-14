import { describe, it, expect } from 'vitest'
import {
  clampFrozenColumns,
  isStickyHeaderEnabled,
  MAX_FROZEN_COLUMNS,
} from '@/stores/database'

// ── clampFrozenColumns ────────────────────────────────────────────────────────

describe('clampFrozenColumns', () => {
  it('defaults missing input to 0', () => {
    expect(clampFrozenColumns(undefined)).toBe(0)
    expect(clampFrozenColumns(null)).toBe(0)
  })

  it('returns 0 for NaN', () => {
    expect(clampFrozenColumns(Number.NaN)).toBe(0)
  })

  it('passes through valid values', () => {
    expect(clampFrozenColumns(0)).toBe(0)
    expect(clampFrozenColumns(1)).toBe(1)
    expect(clampFrozenColumns(MAX_FROZEN_COLUMNS)).toBe(MAX_FROZEN_COLUMNS)
  })

  it('clamps below 0 to 0', () => {
    expect(clampFrozenColumns(-1)).toBe(0)
    expect(clampFrozenColumns(-99)).toBe(0)
  })

  it('clamps above the maximum to the maximum', () => {
    expect(clampFrozenColumns(MAX_FROZEN_COLUMNS + 1)).toBe(MAX_FROZEN_COLUMNS)
    expect(clampFrozenColumns(100)).toBe(MAX_FROZEN_COLUMNS)
  })

  it('floors fractional values', () => {
    expect(clampFrozenColumns(1.9)).toBe(1)
    expect(clampFrozenColumns(2.1)).toBe(2)
  })

  it('exposes a maximum of 3', () => {
    expect(MAX_FROZEN_COLUMNS).toBe(3)
  })
})

// ── isStickyHeaderEnabled ─────────────────────────────────────────────────────

describe('isStickyHeaderEnabled', () => {
  it('defaults to true when the field is absent', () => {
    expect(isStickyHeaderEnabled({})).toBe(true)
    expect(isStickyHeaderEnabled(undefined)).toBe(true)
    expect(isStickyHeaderEnabled(null)).toBe(true)
  })

  it('respects an explicit true', () => {
    expect(isStickyHeaderEnabled({ stickyHeader: true })).toBe(true)
  })

  it('respects an explicit false (opt-out)', () => {
    expect(isStickyHeaderEnabled({ stickyHeader: false })).toBe(false)
  })
})
