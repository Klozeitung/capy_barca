import { describe, it, expect } from 'vitest'
import { displayValue, formatPeriodKey, getTimelineDisplayMode } from '../cellUtils'
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
    ).toBe('14.08.2136 17:13 → 04.05.2137: Torik · 29.01.2143 → 01.09.2174: Irena')
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
    ).toBe('14.08.2136 17:13 → 04.05.2137: Torik · 01.01.2138 → 01.01.2139')
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
      '27.06.1996 → 22.08.2019: Aktiv · 22.08.2019 → 30.01.2020: Nonexistent',
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
      '14.08.2136 17:13 → 04.05.2137: Torik erfolgreich gewählt, Irena',
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

// ── formatPeriodKey ───────────────────────────────────────────────────────────
//
// Timeline period boundaries must honour the user's date-format preference
// instead of the previous hard-coded ISO ``slice(0, 10)``. The format is read
// from the auth store; an explicit token can be passed for deterministic tests.
// Outside an active Pinia instance the store read is guarded and degrades to
// the DD.MM.YYYY fallback.

describe('formatPeriodKey', () => {
  it('renders the always-valid key as the infinity sentinel', () => {
    expect(formatPeriodKey('')).toBe('∞')
    expect(formatPeriodKey('', 'YYYY-MM-DD')).toBe('∞')
  })

  it('formats both boundaries with an explicit user format', () => {
    expect(
      formatPeriodKey('2136-08-14T00:00:00→2137-05-04T00:00:00', 'DD.MM.YYYY'),
    ).toBe('14.08.2136 → 04.05.2137')
    expect(
      formatPeriodKey('2136-08-14T00:00:00→2137-05-04T00:00:00', 'YYYY-MM-DD'),
    ).toBe('2136-08-14 → 2137-05-04')
    expect(
      formatPeriodKey('2136-08-14T00:00:00→2137-05-04T00:00:00', 'MM.DD.YYYY'),
    ).toBe('08.14.2136 → 05.04.2137')
  })

  it('shows the time component only when it is not midnight', () => {
    expect(
      formatPeriodKey('2136-08-14T17:13:00→2137-05-04T00:00:00', 'DD.MM.YYYY'),
    ).toBe('14.08.2136 17:13 → 04.05.2137')
  })

  it('renders an open-ended (since) range', () => {
    expect(formatPeriodKey('2136-08-14T00:00:00→', 'DD.MM.YYYY')).toBe('14.08.2136 →')
    expect(formatPeriodKey('2136-08-14T17:13:00→', 'DD.MM.YYYY')).toBe('14.08.2136 17:13 →')
  })

  it('renders an until range', () => {
    expect(formatPeriodKey('→2137-05-04T00:00:00', 'DD.MM.YYYY')).toBe('→ 04.05.2137')
    expect(formatPeriodKey('→2137-05-04T09:30:00', 'YYYY-MM-DD')).toBe('→ 2137-05-04 09:30')
  })

  it('falls back to DD.MM.YYYY when no user format is available', () => {
    expect(
      formatPeriodKey('2136-08-14T00:00:00→2137-05-04T00:00:00'),
    ).toBe('14.08.2136 → 04.05.2137')
  })
})

// ── getTimelineDisplayMode ─────────────────────────────────────────────────────
//
// Timelined properties default to "all" (every slot shown as a period → value
// row). An explicit config.timelineDisplayMode always wins. The "last"/non-set
// distinction matters because the cells gate their slot-list rendering on the
// resolved mode being exactly 'all'.

describe('getTimelineDisplayMode', () => {
  function schemaWith(config: Record<string, unknown>): PropertySchema {
    return {
      id: 'schema-x',
      database_id: 'db-1',
      name: 'X',
      type: 'text',
      config,
      position: 0,
      group: 'Standard',
    }
  }

  it('defaults to "all" when no display mode is set', () => {
    expect(getTimelineDisplayMode(schemaWith({ hasTimeline: true }))).toBe('all')
    expect(getTimelineDisplayMode(schemaWith({}))).toBe('all')
  })

  it('respects an explicit mode', () => {
    expect(getTimelineDisplayMode(schemaWith({ timelineDisplayMode: 'last' }))).toBe('last')
    expect(getTimelineDisplayMode(schemaWith({ timelineDisplayMode: 'all' }))).toBe('all')
    expect(getTimelineDisplayMode(schemaWith({ timelineDisplayMode: 'now' }))).toBe('now')
  })
})
