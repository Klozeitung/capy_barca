import { describe, it, expect } from 'vitest'
import { displayValue } from '../cellUtils'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'

// ── Fixtures ──────────────────────────────────────────────────────────────────

function relationSchema(config: Record<string, unknown> = {}): PropertySchema {
  return {
    id: 'schema-partner',
    database_id: 'db-1',
    name: 'Partner',
    type: 'relation',
    config: {
      hasTimeline: true,
      timelineDisplayMode: 'all',
      target_database_id: 'db-1',
      ...config,
    },
    position: 0,
    group: 'Standard',
  }
}

function entryWithPartner(value: Record<string, unknown> | null): DatabaseEntry {
  return {
    id: 'entry-1',
    position: 0,
    content: { title: 'Lete' },
    icon: null,
    state: 'active',
    values: { 'schema-partner': value },
  }
}

const TITLES: Record<string, string> = {
  'torik-id': 'Torik',
  'irena-id': 'Irena',
}
const resolveTitle = (id: string): string => TITLES[id] ?? ''

// ── Timeline relation export ("all" mode) ─────────────────────────────────────

describe('displayValue – timeline relation export ("all" mode)', () => {
  it('resolves relation chips to titles for each bounded slot', () => {
    const value = {
      relationPool: {
        'torik-id': ['2136-08-14T17:13:00→2137-05-04T00:00:00'],
        'irena-id': ['2143-01-29T00:00:00→2174-09-01T00:00:00'],
      },
      _timeline: {
        '2136-08-14T17:13:00→2137-05-04T00:00:00': { related_ids: ['torik-id'] },
        '2143-01-29T00:00:00→2174-09-01T00:00:00': { related_ids: ['irena-id'] },
      },
    }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('2136-08-14 → 2137-05-04: Torik · 2143-01-29 → 2174-09-01: Irena')
  })

  it('renders an always-valid ("") slot as names only, without a period', () => {
    const value = { _timeline: { '': { related_ids: ['torik-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('Torik')
  })

  it('comma-joins multiple linked entries within a single slot', () => {
    const value = { _timeline: { '': { related_ids: ['torik-id', 'irena-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('Torik, Irena')
  })

  it('falls back to the raw id when a title cannot be resolved', () => {
    const value = { _timeline: { '': { related_ids: ['unknown-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('unknown-id')
  })

  it('falls back to raw ids when no resolver is supplied', () => {
    const value = { _timeline: { '': { related_ids: ['torik-id'] } } }
    expect(displayValue(entryWithPartner(value), relationSchema())).toBe('torik-id')
  })

  it('emits the period only for a slot with no linked entries', () => {
    const value = {
      _timeline: {
        '2136-08-14T17:13:00→2137-05-04T00:00:00': { related_ids: ['torik-id'] },
        '2138-01-01T00:00:00→2139-01-01T00:00:00': { related_ids: [] },
      },
    }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('2136-08-14 → 2137-05-04: Torik · 2138-01-01 → 2139-01-01')
  })
})

// ── Regression guard: scalar "all" mode must stay intact ──────────────────────

describe('displayValue – timeline scalar export ("all" mode) is unaffected', () => {
  it('still renders select slots as "period: value"', () => {
    const schema: PropertySchema = {
      id: 'schema-status',
      database_id: 'db-1',
      name: 'Status',
      type: 'select',
      config: { hasTimeline: true, timelineDisplayMode: 'all', mode: 'single' },
      position: 0,
      group: 'Standard',
    }
    const entry: DatabaseEntry = {
      id: 'entry-2',
      position: 0,
      content: null,
      icon: null,
      state: 'active',
      values: {
        'schema-status': {
          _timeline: {
            '1996-06-27T00:00:00→2019-08-22T00:00:00': { option: 'Aktiv' },
            '2019-08-22T00:00:00→2020-01-30T00:00:00': { option: 'Nonexistent' },
          },
        },
      },
    }
    expect(displayValue(entry, schema)).toBe(
      '1996-06-27 → 2019-08-22: Aktiv · 2019-08-22 → 2020-01-30: Nonexistent',
    )
  })
})

// ── Relation nuance ───────────────────────────────────────────────────────────

import {
  getNuanceConfig,
  nuanceLabelFor,
  formatNuancedRelation,
  type NuanceConfig,
} from '../cellUtils'

function nuanceConfig(over: Partial<NuanceConfig> = {}): NuanceConfig {
  return { enabled: true, options: [], affix1: '', affix2: '', orientation: 'prepended', ...over }
}

describe('getNuanceConfig', () => {
  it('returns null when nuance is absent or disabled', () => {
    expect(getNuanceConfig(relationSchema())).toBeNull()
    expect(getNuanceConfig(relationSchema({ nuance: { enabled: false } }))).toBeNull()
  })

  it('reads affixes and orientation, defaulting orientation to prepended', () => {
    const cfg = getNuanceConfig(relationSchema({
      nuance: { enabled: true, affix1: 'as', affix2: 'of', orientation: 'appended' },
    }))
    expect(cfg).toMatchObject({ affix1: 'as', affix2: 'of', orientation: 'appended' })

    const cfg2 = getNuanceConfig(relationSchema({ nuance: { enabled: true } }))
    expect(cfg2?.orientation).toBe('prepended')
  })
})

describe('formatNuancedRelation', () => {
  const cfg = nuanceConfig

  it('returns the bare title when there is no label or no config', () => {
    expect(formatNuancedRelation('Torik', '', cfg())).toBe('Torik')
    expect(formatNuancedRelation('Torik', 'lead', null)).toBe('Torik')
  })

  it('prepends the nuance group before the title (prepended)', () => {
    expect(formatNuancedRelation('Großmeister', 'erfolgreich', cfg())).toBe('erfolgreich Großmeister')
    expect(
      formatNuancedRelation('Großmeister', 'erfolgreich', cfg({ affix1: 'als', affix2: 'gewählt' })),
    ).toBe('als erfolgreich gewählt Großmeister')
  })

  it('appends the nuance group after the title (appended)', () => {
    expect(
      formatNuancedRelation('Großmeister', 'erfolgreich', cfg({ orientation: 'appended', affix1: 'als', affix2: 'bestätigt' })),
    ).toBe('Großmeister als erfolgreich bestätigt')
  })
})

describe('nuanceLabelFor', () => {
  it('reads the label for a related id, empty when missing', () => {
    const slot = { related_ids: ['a'], nuances: { a: 'lead' } }
    expect(nuanceLabelFor(slot, 'a')).toBe('lead')
    expect(nuanceLabelFor(slot, 'b')).toBe('')
    expect(nuanceLabelFor(null, 'a')).toBe('')
  })
})

describe('displayValue – nuanced timeline relation', () => {
  it('renders the nuance group in an always-valid slot ("all" mode)', () => {
    const schema = relationSchema({ nuance: nuanceConfig() })
    const value = { _timeline: { '': { related_ids: ['torik-id'], nuances: { 'torik-id': 'erfolgreich' } } } }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveTitle)).toBe('erfolgreich Torik')
  })

  it('renders nuance per related entry within a bounded slot', () => {
    const schema = relationSchema({ nuance: nuanceConfig({ orientation: 'appended', affix2: 'gewählt' }) })
    const value = {
      _timeline: {
        '2136-08-14T17:13:00→2137-05-04T00:00:00': {
          related_ids: ['torik-id', 'irena-id'],
          nuances: { 'torik-id': 'erfolgreich' },
        },
      },
    }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveTitle)).toBe(
      '2136-08-14 → 2137-05-04: Torik erfolgreich gewählt, Irena',
    )
  })
})

describe('displayValue – nuanced flat relation ("last" mode)', () => {
  it('renders the nuance group for a non-timeline relation', () => {
    const schema: PropertySchema = {
      id: 'schema-partner',
      database_id: 'db-1',
      name: 'Partner',
      type: 'relation',
      config: { target_database_id: 'db-1', nuance: nuanceConfig() },
      position: 0,
      group: 'Standard',
    }
    const value = { related_ids: ['torik-id'], nuances: { 'torik-id': 'erfolgreich' } }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveTitle)).toBe('erfolgreich Torik')
  })
})
