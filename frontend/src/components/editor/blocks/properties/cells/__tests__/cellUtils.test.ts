import { describe, it, expect } from 'vitest'
import { DEFAULT_ENTRY_ICON, chipIcon, displayValue, formatPeriodKey, getTimelineDisplayMode } from '../cellUtils'
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
    content: { title: 'Source' },
    icon: null,
    state: 'active',
    values: { 'schema-partner': value },
  }
}

const TITLES: Record<string, string> = {
  'alpha-id': 'Alpha',
  'beta-id': 'Beta',
}
const resolveTitle = (id: string): string => TITLES[id] ?? ''

// ── Timeline relation export ("all" mode) ─────────────────────────────────────

describe('displayValue – timeline relation export ("all" mode)', () => {
  it('resolves relation chips to titles for each bounded slot', () => {
    const value = {
      relationPool: {
        'alpha-id': ['2100-03-10T08:45:00→2101-06-20T00:00:00'],
        'beta-id': ['2105-02-11T00:00:00→2140-11-05T00:00:00'],
      },
      _timeline: {
        '2100-03-10T08:45:00→2101-06-20T00:00:00': { related_ids: ['alpha-id'] },
        '2105-02-11T00:00:00→2140-11-05T00:00:00': { related_ids: ['beta-id'] },
      },
    }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('10.03.2100 08:45 → 20.06.2101: Alpha · 11.02.2105 → 05.11.2140: Beta')
  })

  it('renders an always-valid ("") slot as names only, without a period', () => {
    const value = { _timeline: { '': { related_ids: ['alpha-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('Alpha')
  })

  it('comma-joins multiple linked entries within a single slot', () => {
    const value = { _timeline: { '': { related_ids: ['alpha-id', 'beta-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('Alpha, Beta')
  })

  it('falls back to the raw id when a title cannot be resolved', () => {
    const value = { _timeline: { '': { related_ids: ['unknown-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('unknown-id')
  })

  it('falls back to raw ids when no resolver is supplied', () => {
    const value = { _timeline: { '': { related_ids: ['alpha-id'] } } }
    expect(displayValue(entryWithPartner(value), relationSchema())).toBe('alpha-id')
  })

  it('emits the period only for a slot with no linked entries', () => {
    const value = {
      _timeline: {
        '2100-03-10T08:45:00→2101-06-20T00:00:00': { related_ids: ['alpha-id'] },
        '2138-01-01T00:00:00→2139-01-01T00:00:00': { related_ids: [] },
      },
    }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('10.03.2100 08:45 → 20.06.2101: Alpha · 01.01.2138 → 01.01.2139')
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
      config: {
        hasTimeline: true, timelineDisplayMode: 'all', mode: 'single',
        options: [{ id: 'o-active', label: 'Active' }, { id: 'o-other', label: 'Other' }],
      },
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
            '1990-04-12T00:00:00→2000-09-18T00:00:00': { option: 'o-active' },
            '2000-09-18T00:00:00→2001-03-02T00:00:00': { option: 'o-other' },
          },
        },
      },
    }
    expect(displayValue(entry, schema)).toBe(
      '12.04.1990 → 18.09.2000: Active · 18.09.2000 → 02.03.2001: Other',
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

const NUANCE_OPTIONS = [
  { id: 'n-success', label: 'successfully' },
  { id: 'n-former', label: 'formerly' },
]

function nuanceConfig(over: Partial<NuanceConfig> = {}): NuanceConfig {
  return {
    enabled: true, options: NUANCE_OPTIONS, affix1: '', affix2: '', orientation: 'prepended', ...over,
  }
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
    expect(formatNuancedRelation('Alpha', '', cfg())).toBe('Alpha')
    expect(formatNuancedRelation('Alpha', 'lead', null)).toBe('Alpha')
  })

  it('prepends the nuance group before the title (prepended)', () => {
    expect(formatNuancedRelation('Role X', 'successfully', cfg())).toBe('successfully Role X')
    expect(
      formatNuancedRelation('Role X', 'successfully', cfg({ affix1: 'as', affix2: 'elected' })),
    ).toBe('as successfully elected Role X')
  })

  it('appends the nuance group after the title (appended)', () => {
    expect(
      formatNuancedRelation('Role X', 'successfully', cfg({ orientation: 'appended', affix1: 'as', affix2: 'confirmed' })),
    ).toBe('Role X as successfully confirmed')
  })
})

describe('nuanceLabelFor', () => {
  it('reads the label of the stored option id, empty when missing', () => {
    const slot = { related_ids: ['a', 'c'], nuances: { a: 'n-success', c: 'n-gone' } }
    expect(nuanceLabelFor(slot, 'a', nuanceConfig())).toBe('successfully')
    expect(nuanceLabelFor(slot, 'b', nuanceConfig())).toBe('')
    expect(nuanceLabelFor(slot, 'c', nuanceConfig())).toBe('')
    expect(nuanceLabelFor(null, 'a', nuanceConfig())).toBe('')
    expect(nuanceLabelFor(slot, 'a', null)).toBe('')
  })

  it('follows a renamed option', () => {
    const slot = { related_ids: ['a'], nuances: { a: 'n-success' } }
    const renamed = nuanceConfig({ options: [{ id: 'n-success', label: 'barely' }] })
    expect(nuanceLabelFor(slot, 'a', renamed)).toBe('barely')
  })
})

describe('displayValue – nuanced timeline relation', () => {
  it('renders the nuance group in an always-valid slot ("all" mode)', () => {
    const schema = relationSchema({ nuance: nuanceConfig() })
    const value = { _timeline: { '': { related_ids: ['alpha-id'], nuances: { 'alpha-id': 'n-success' } } } }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveTitle)).toBe('successfully Alpha')
  })

  it('renders nuance per related entry within a bounded slot', () => {
    const schema = relationSchema({ nuance: nuanceConfig({ orientation: 'appended', affix2: 'elected' }) })
    const value = {
      _timeline: {
        '2100-03-10T08:45:00→2101-06-20T00:00:00': {
          related_ids: ['alpha-id', 'beta-id'],
          nuances: { 'alpha-id': 'n-success' },
        },
      },
    }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveTitle)).toBe(
      '10.03.2100 08:45 → 20.06.2101: Alpha successfully elected, Beta',
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
    const value = { related_ids: ['alpha-id'], nuances: { 'alpha-id': 'n-success' } }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveTitle)).toBe('successfully Alpha')
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
      formatPeriodKey('2100-03-10T00:00:00→2101-06-20T00:00:00', 'DD.MM.YYYY'),
    ).toBe('10.03.2100 → 20.06.2101')
    expect(
      formatPeriodKey('2100-03-10T00:00:00→2101-06-20T00:00:00', 'YYYY-MM-DD'),
    ).toBe('2100-03-10 → 2101-06-20')
    expect(
      formatPeriodKey('2100-03-10T00:00:00→2101-06-20T00:00:00', 'MM.DD.YYYY'),
    ).toBe('03.10.2100 → 06.20.2101')
  })

  it('shows the time component only when it is not midnight', () => {
    expect(
      formatPeriodKey('2100-03-10T08:45:00→2101-06-20T00:00:00', 'DD.MM.YYYY'),
    ).toBe('10.03.2100 08:45 → 20.06.2101')
  })

  it('renders an open-ended (since) range', () => {
    expect(formatPeriodKey('2100-03-10T00:00:00→', 'DD.MM.YYYY')).toBe('10.03.2100 →')
    expect(formatPeriodKey('2100-03-10T08:45:00→', 'DD.MM.YYYY')).toBe('10.03.2100 08:45 →')
  })

  it('renders an until range', () => {
    expect(formatPeriodKey('→2101-06-20T00:00:00', 'DD.MM.YYYY')).toBe('→ 20.06.2101')
    expect(formatPeriodKey('→2101-06-20T09:30:00', 'YYYY-MM-DD')).toBe('→ 2101-06-20 09:30')
  })

  it('falls back to DD.MM.YYYY when no user format is available', () => {
    expect(
      formatPeriodKey('2100-03-10T00:00:00→2101-06-20T00:00:00'),
    ).toBe('10.03.2100 → 20.06.2101')
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

// ── Formula date-range result ─────────────────────────────────────────────────
//
// A formula that yields a date property directly (prop('date')) produces a
// {start, end} object, not an ISO string. It must render like a date cell
// ("start → end") instead of leaking raw JSON. The type guard also has to
// reject relation descriptors, which are objects too but carry no start.

import {
  isDateRangeResult,
  formatDateRangeResult,
  formatFormulaExport,
} from '../cellUtils'

describe('isDateRangeResult', () => {
  it('accepts a {start, end} object with an ISO start', () => {
    expect(isDateRangeResult({ start: '2000-01-01T00:01', end: '2000-01-02T00:01' })).toBe(true)
    expect(isDateRangeResult({ start: '2000-01-01', end: null })).toBe(true)
  })

  it('rejects non-date-range shapes', () => {
    expect(isDateRangeResult(null)).toBe(false)
    expect(isDateRangeResult('2000-01-01')).toBe(false)
    expect(isDateRangeResult(['2000-01-01'])).toBe(false)
    expect(isDateRangeResult({ id: 'u1', title: 'X', database_id: 'db' })).toBe(false)
    expect(isDateRangeResult({ start: 'not-a-date' })).toBe(false)
  })
})

describe('formatDateRangeResult', () => {
  it('renders a single boundary when there is no distinct end', () => {
    expect(formatDateRangeResult({ start: '2000-01-01T00:01', end: null }, 'DD.MM.YYYY')).toBe('01.01.2000 00:01')
    expect(formatDateRangeResult({ start: '2000-01-01T00:01', end: '2000-01-01T00:01' }, 'DD.MM.YYYY')).toBe('01.01.2000 00:01')
  })

  it('renders "start → end" for a distinct end', () => {
    expect(
      formatDateRangeResult({ start: '2000-01-01T00:01', end: '2000-01-02T00:01' }, 'DD.MM.YYYY'),
    ).toBe('01.01.2000 00:01 → 02.01.2000 00:01')
  })

  it('drops midnight time components', () => {
    expect(
      formatDateRangeResult({ start: '2000-01-01T00:00:00', end: '2000-01-02T00:00:00' }, 'DD.MM.YYYY'),
    ).toBe('01.01.2000 → 02.01.2000')
  })

  it('returns empty when there is no start', () => {
    expect(formatDateRangeResult({ start: '', end: null }, 'DD.MM.YYYY')).toBe('')
  })
})

describe('formatFormulaExport – date-range object result', () => {
  function formulaSchema(): PropertySchema {
    return {
      id: 'schema-f',
      database_id: 'db-1',
      name: 'F',
      type: 'formula',
      config: { expression: "prop('date')", dateFormat: 'DD.MM.YYYY' },
      position: 0,
      group: 'Standard',
    }
  }

  it('formats a {start, end} result instead of leaking JSON', () => {
    const value = {
      result: { start: '2000-01-01T00:01', end: '2000-01-02T00:01' },
      result_type: 'date',
    }
    expect(formatFormulaExport(value, formulaSchema())).toBe('01.01.2000 00:01 → 02.01.2000 00:01')
  })

  it('formats a start-only result as a single date', () => {
    const value = { result: { start: '2000-01-01T00:00:00', end: null }, result_type: 'date' }
    expect(formatFormulaExport(value, formulaSchema())).toBe('01.01.2000')
  })
})

// ── Relation keying ───────────────────────────────────────────────────────────
//
// Keying orders a relation's linked entries by a property of the target
// database and renders that value beside each chip. Everything below is a pure
// view transform: the caller supplies the key values, and the id array handed in
// is never mutated.

import {
  getKeyingConfig,
  isKeyableProperty,
  keyComparable,
  sortIdsByKey,
  formatKeyValue,
  KEYABLE_PROPERTY_TYPES,
  type KeyingConfig,
} from '../cellUtils'

function targetSchema(type: string, config: Record<string, unknown> = {}): PropertySchema {
  return {
    id: `schema-${type}`,
    database_id: 'db-target',
    name: type,
    type,
    config,
    position: 0,
    group: 'Standard',
  }
}

function keyingSchema(over: Record<string, unknown> = {}): PropertySchema {
  return {
    id: 'schema-events',
    database_id: 'db-1',
    name: 'Events',
    type: 'relation',
    config: {
      target_database_id: 'db-target',
      keying: {
        enabled: true,
        key_property_id: 'schema-date',
        key_order: 'asc',
        key_empty_first: false,
        ...over,
      },
    },
    position: 0,
    group: 'Standard',
  }
}

const ASC: KeyingConfig = { keyPropertyId: 'schema-date', order: 'asc', emptyFirst: false }

describe('isKeyableProperty', () => {
  it('accepts exactly the four supported types', () => {
    for (const type of KEYABLE_PROPERTY_TYPES) {
      expect(isKeyableProperty(targetSchema(type))).toBe(true)
    }
  })

  it('rejects everything else', () => {
    for (const type of ['checkbox', 'relation', 'rollup', 'formula', 'file', 'id']) {
      expect(isKeyableProperty(targetSchema(type))).toBe(false)
    }
  })
})

describe('getKeyingConfig', () => {
  it('returns null when keying is absent or disabled', () => {
    const bare: PropertySchema = { ...keyingSchema(), config: { target_database_id: 'db-target' } }
    expect(getKeyingConfig(bare)).toBeNull()
    expect(getKeyingConfig(keyingSchema({ enabled: false }))).toBeNull()
  })

  it('treats an enabled block without a property id as absent', () => {
    expect(getKeyingConfig(keyingSchema({ key_property_id: '' }))).toBeNull()
  })

  it('reads the pointer, order and empty placement', () => {
    expect(getKeyingConfig(keyingSchema())).toEqual({
      keyPropertyId: 'schema-date',
      order: 'asc',
      emptyFirst: false,
    })
    expect(
      getKeyingConfig(keyingSchema({ key_order: 'desc', key_empty_first: true })),
    ).toMatchObject({ order: 'desc', emptyFirst: true })
  })

  it('defaults an unknown order to ascending', () => {
    expect(getKeyingConfig(keyingSchema({ key_order: 'sideways' }))?.order).toBe('asc')
  })
})

describe('keyComparable', () => {
  it('reads a date property\'s start boundary', () => {
    const schema = targetSchema('date')
    expect(keyComparable({ start: '2100-03-10' }, schema)).toBe('2100-03-10')
    expect(keyComparable({ start: '' }, schema)).toBeNull()
    expect(keyComparable({ end: '2100-03-10' }, schema)).toBeNull()
  })

  it('reads a number property numerically, including zero', () => {
    const schema = targetSchema('number')
    expect(keyComparable({ number: 12 }, schema)).toBe(12)
    expect(keyComparable({ number: 0 }, schema)).toBe(0)
    expect(keyComparable({ number: '7' }, schema)).toBe(7)
    expect(keyComparable({ number: 'x' }, schema)).toBeNull()
    expect(keyComparable({}, schema)).toBeNull()
  })

  it('reads a select property as its option index, not its label', () => {
    const schema = targetSchema('select', { options: [
      { id: 's3', label: 'Stage 3' }, { id: 's1', label: 'Stage 1' }, { id: 's2', label: 'Stage 2' },
    ] })
    expect(keyComparable({ option: 's3' }, schema)).toBe(0)
    expect(keyComparable({ option: 's1' }, schema)).toBe(1)
    expect(keyComparable({ option: 'gone' }, schema)).toBeNull()
  })

  it('reads a multi-select by its earliest option', () => {
    const schema = targetSchema('select', {
      mode: 'multiple',
      options: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }, { id: 'c', label: 'C' }],
    })
    expect(keyComparable({ options: ['c', 'b'] }, schema)).toBe(1)
    expect(keyComparable({ options: [] }, schema)).toBeNull()
  })

  it('reads a text property, treating whitespace as empty', () => {
    const schema = targetSchema('text')
    expect(keyComparable({ text: 'Aleph' }, schema)).toBe('Aleph')
    expect(keyComparable({ text: '   ' }, schema)).toBeNull()
  })

  it('returns null without a value or a schema', () => {
    expect(keyComparable(null, targetSchema('date'))).toBeNull()
    expect(keyComparable({ start: '2100-03-10' }, null)).toBeNull()
  })
})

describe('sortIdsByKey', () => {
  const dateSchema = targetSchema('date')
  const dates: Record<string, Record<string, unknown> | null> = {
    late: { start: '2140-11-05' },
    early: { start: '2100-03-10' },
    middle: { start: '2105-02-11' },
    undated: null,
  }
  const lookup = (id: string) => dates[id] ?? null

  it('orders ascending by the key value', () => {
    expect(sortIdsByKey(['late', 'early', 'middle'], lookup, dateSchema, ASC)).toEqual([
      'early', 'middle', 'late',
    ])
  })

  it('orders descending', () => {
    expect(
      sortIdsByKey(['early', 'late', 'middle'], lookup, dateSchema, { ...ASC, order: 'desc' }),
    ).toEqual(['late', 'middle', 'early'])
  })

  it('places entries without a key value last by default', () => {
    expect(sortIdsByKey(['undated', 'late', 'early'], lookup, dateSchema, ASC)).toEqual([
      'early', 'late', 'undated',
    ])
  })

  it('places them first when emptyFirst is set', () => {
    expect(
      sortIdsByKey(['late', 'undated', 'early'], lookup, dateSchema, { ...ASC, emptyFirst: true }),
    ).toEqual(['undated', 'early', 'late'])
  })

  it('keeps the empty placement independent of the order direction', () => {
    const desc = { ...ASC, order: 'desc' as const }
    expect(sortIdsByKey(['undated', 'early', 'late'], lookup, dateSchema, desc)).toEqual([
      'late', 'early', 'undated',
    ])
  })

  it('breaks ties by the incoming (seniority) order', () => {
    const tied: Record<string, Record<string, unknown>> = {
      b: { start: '2140-01-01' },
      a: { start: '2140-01-01' },
      c: { start: '2130-01-01' },
    }
    expect(
      sortIdsByKey(['b', 'a', 'c'], (id: string) => tied[id], dateSchema, ASC),
    ).toEqual(['c', 'b', 'a'])
  })

  it('keeps the incoming order among entries without a key value', () => {
    expect(
      sortIdsByKey(['z', 'y', 'early'], lookup, dateSchema, { ...ASC, emptyFirst: true }),
    ).toEqual(['z', 'y', 'early'])
  })

  it('returns the input unchanged when the key property cannot be resolved', () => {
    const input = ['late', 'early']
    expect(sortIdsByKey(input, lookup, null, ASC)).toEqual(input)
  })

  it('does not mutate the array it was given', () => {
    const input = ['late', 'early', 'middle']
    sortIdsByKey(input, lookup, dateSchema, ASC)
    expect(input).toEqual(['late', 'early', 'middle'])
  })

  it('sorts numbers numerically rather than lexicographically', () => {
    const numberSchema = targetSchema('number')
    const values: Record<string, Record<string, unknown>> = {
      ten: { number: 10 },
      two: { number: 2 },
      hundred: { number: 100 },
    }
    expect(
      sortIdsByKey(['ten', 'hundred', 'two'], (id: string) => values[id], numberSchema, ASC),
    ).toEqual(['two', 'ten', 'hundred'])
  })

  it('sorts selects by option order rather than alphabetically', () => {
    const selectSchema = targetSchema('select', {
      options: [{ id: 'o-zeta', label: 'Zeta' }, { id: 'o-alpha', label: 'Alpha' }],
    })
    const values: Record<string, Record<string, unknown>> = {
      alpha: { option: 'o-alpha' },
      zeta: { option: 'o-zeta' },
    }
    expect(
      sortIdsByKey(['alpha', 'zeta'], (id: string) => values[id], selectSchema, ASC),
    ).toEqual(['zeta', 'alpha'])
  })
})

describe('formatKeyValue', () => {
  it('renders a date with the key property\'s own format', () => {
    const schema = targetSchema('date', { dateFormat: 'YYYY-MM-DD' })
    expect(formatKeyValue({ start: '2100-03-10T00:00:00' }, schema)).toBe('2100-03-10')
  })

  it('renders a select label and a plain number', () => {
    const select = targetSchema('select', { options: [{ id: 's1', label: 'Stage 1' }] })
    expect(formatKeyValue({ option: 's1' }, select)).toBe('Stage 1')
    expect(formatKeyValue({ number: 12 }, targetSchema('number'))).toBe('12')
  })

  it('returns empty without a value or a schema', () => {
    expect(formatKeyValue(null, targetSchema('date'))).toBe('')
    expect(formatKeyValue({ start: '2100-03-10' }, null)).toBe('')
  })
})

// ── Keyed relation export ─────────────────────────────────────────────────────
//
// displayValue is the plain-text path behind CSV, XLSX and PDF. A keyed
// relation must export in key order with each entry prefixed by its key value,
// mirroring the two-zone cell — and must fall back to the vanilla rendering for
// every caller that supplies no resolver.

import type { RelationKeyResolver } from '../cellUtils'

function keyedRelationSchema(over: Record<string, unknown> = {}): PropertySchema {
  return {
    id: 'schema-events',
    database_id: 'db-characters',
    name: 'Events',
    type: 'relation',
    config: {
      target_database_id: 'db-events',
      keying: {
        enabled: true,
        key_property_id: 'schema-eventdate',
        key_order: 'asc',
        key_empty_first: false,
        ...over,
      },
    },
    position: 0,
    group: 'Standard',
  }
}

function entryWithEvents(relatedIds: string[]): DatabaseEntry {
  return {
    id: 'character-1',
    position: 0,
    content: { title: 'Source' },
    icon: null,
    state: 'active',
    values: { 'schema-events': { related_ids: relatedIds } },
  }
}

const EVENT_TITLES: Record<string, string> = {
  'event-late': 'Event B',
  'event-early': 'Event A',
  'event-undated': 'Sometime in Stage 3',
}
const resolveEventTitle = (id: string): string => EVENT_TITLES[id] ?? ''

const EVENT_DATES: Record<string, Record<string, unknown> | null> = {
  'event-late': { start: '2105-02-11' },
  'event-early': { start: '2100-03-10' },
  'event-undated': null,
}

const eventDateSchema: PropertySchema = {
  id: 'schema-eventdate',
  database_id: 'db-events',
  name: 'Date',
  type: 'date',
  config: {},
  position: 0,
  group: 'Standard',
}

const keyResolver: RelationKeyResolver = {
  schemaFor: (id: string) => (id === 'schema-eventdate' ? eventDateSchema : null),
  valueFor: (keyPropertyId: string, entryId: string) =>
    keyPropertyId === 'schema-eventdate' ? (EVENT_DATES[entryId] ?? null) : null,
}

describe('displayValue – keyed relation export', () => {
  it('orders the linked entries by their key value and prefixes each', () => {
    expect(
      displayValue(
        entryWithEvents(['event-late', 'event-early']),
        keyedRelationSchema(),
        undefined,
        resolveEventTitle,
        keyResolver,
      ),
    ).toBe('10.03.2100: Event A · 11.02.2105: Event B')
  })

  it('honours a descending order', () => {
    expect(
      displayValue(
        entryWithEvents(['event-early', 'event-late']),
        keyedRelationSchema({ key_order: 'desc' }),
        undefined,
        resolveEventTitle,
        keyResolver,
      ),
    ).toBe('11.02.2105: Event B · 10.03.2100: Event A')
  })

  it('renders an entry without a key value as the bare title, placed last', () => {
    expect(
      displayValue(
        entryWithEvents(['event-undated', 'event-early']),
        keyedRelationSchema(),
        undefined,
        resolveEventTitle,
        keyResolver,
      ),
    ).toBe('10.03.2100: Event A · Sometime in Stage 3')
  })

  it('honours the empty-first placement', () => {
    expect(
      displayValue(
        entryWithEvents(['event-early', 'event-undated']),
        keyedRelationSchema({ key_empty_first: true }),
        undefined,
        resolveEventTitle,
        keyResolver,
      ),
    ).toBe('Sometime in Stage 3 · 10.03.2100: Event A')
  })

  it('falls back to the vanilla export when no resolver is supplied', () => {
    // Every existing caller passes four arguments and must be unaffected.
    expect(
      displayValue(
        entryWithEvents(['event-late', 'event-early']),
        keyedRelationSchema(),
        undefined,
        resolveEventTitle,
      ),
    ).toBe('Event B, Event A')
  })

  it('falls back to the vanilla export when the key property no longer resolves', () => {
    const dangling: RelationKeyResolver = { schemaFor: () => null, valueFor: () => null }
    expect(
      displayValue(
        entryWithEvents(['event-late', 'event-early']),
        keyedRelationSchema(),
        undefined,
        resolveEventTitle,
        dangling,
      ),
    ).toBe('Event B, Event A')
  })

  it('leaves a vanilla relation untouched even when a resolver is present', () => {
    const vanilla: PropertySchema = {
      ...keyedRelationSchema(),
      config: { target_database_id: 'db-events' },
    }
    expect(
      displayValue(
        entryWithEvents(['event-late', 'event-early']),
        vanilla,
        undefined,
        resolveEventTitle,
        keyResolver,
      ),
    ).toBe('Event B, Event A')
  })

  it('prefers the established rendering when legacy data is keyed and nuanced', () => {
    const both = keyedRelationSchema()
    both.config = { ...both.config, nuance: nuanceConfig() }
    const entry = entryWithEvents(['event-late'])
    entry.values['schema-events'] = {
      related_ids: ['event-late'],
      nuances: { 'event-late': 'n-success' },
    }
    expect(
      displayValue(entry, both, undefined, resolveEventTitle, keyResolver),
    ).toBe('successfully Event B')
  })

  it('prefers the timeline rendering when legacy data is keyed and timelined', () => {
    const both = keyedRelationSchema()
    both.config = { ...both.config, hasTimeline: true, timelineDisplayMode: 'all' }
    const entry = entryWithEvents([])
    entry.values['schema-events'] = { _timeline: { '': { related_ids: ['event-early'] } } }
    expect(
      displayValue(entry, both, undefined, resolveEventTitle, keyResolver),
    ).toBe('Event A')
  })

  it('falls back to the raw id when a title cannot be resolved', () => {
    expect(
      displayValue(
        entryWithEvents(['event-early']),
        keyedRelationSchema(),
        undefined,
        () => '',
        keyResolver,
      ),
    ).toBe('10.03.2100: event-early')
  })

  it('returns empty for a relation with no links', () => {
    expect(
      displayValue(entryWithEvents([]), keyedRelationSchema(), undefined, resolveEventTitle, keyResolver),
    ).toBe('')
  })

  it('formats the key with the key property\'s own configuration', () => {
    const isoSchema: PropertySchema = { ...eventDateSchema, config: { dateFormat: 'YYYY-MM-DD' } }
    const isoResolver: RelationKeyResolver = { ...keyResolver, schemaFor: () => isoSchema }
    expect(
      displayValue(
        entryWithEvents(['event-early']),
        keyedRelationSchema(),
        undefined,
        resolveEventTitle,
        isoResolver,
      ),
    ).toBe('2100-03-10: Event A')
  })
})

// ── Links to trashed entries ──────────────────────────────────────────────────
//
// The export title lookup returns null for a linked entry that is known to be
// gone (trashed or deleted). Such links are omitted, mirroring the relation
// cell, which drops the chip, instead of leaking the raw entry id. An empty
// string still means "title unknown" and keeps the raw id.

const resolveWithTrashed = (id: string): string | null =>
  id === 'trashed-id' ? null : (TITLES[id] ?? '')

describe('displayValue – links to trashed entries are omitted', () => {
  it('drops a trashed link from an always-valid timeline slot', () => {
    const value = { _timeline: { '': { related_ids: ['alpha-id', 'trashed-id', 'beta-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveWithTrashed),
    ).toBe('Alpha, Beta')
  })

  it('keeps the period of a bounded slot whose only link is trashed', () => {
    const value = {
      _timeline: {
        '2100-03-10T00:00:00→2101-06-20T00:00:00': { related_ids: ['trashed-id'] },
        '2101-06-21T00:00:00→': { related_ids: ['alpha-id'] },
      },
    }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveWithTrashed),
    ).toBe('10.03.2100 → 20.06.2101 · 21.06.2101 →: Alpha')
  })

  it('drops a trashed link from a flat relation, nuance included', () => {
    const schema = relationSchema({
      hasTimeline: false,
      timelineDisplayMode: undefined,
      nuance: nuanceConfig(),
    })
    const value = {
      related_ids: ['trashed-id', 'alpha-id'],
      nuances: { 'trashed-id': 'n-former', 'alpha-id': 'n-success' },
    }
    expect(
      displayValue(entryWithPartner(value), schema, undefined, resolveWithTrashed),
    ).toBe('successfully Alpha')
  })

  it('returns empty when every link of a flat relation is trashed', () => {
    const schema = relationSchema({ hasTimeline: false, timelineDisplayMode: undefined })
    const value = { related_ids: ['trashed-id'] }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveWithTrashed)).toBe('')
  })

  it('drops a trashed link from a keyed relation before ordering', () => {
    const resolve = (id: string): string | null =>
      id === 'event-late' ? null : (EVENT_TITLES[id] ?? '')
    expect(
      displayValue(
        entryWithEvents(['event-late', 'event-undated', 'event-early']),
        keyedRelationSchema(),
        undefined,
        resolve,
        keyResolver,
      ),
    ).toBe('10.03.2100: Event A · Sometime in Stage 3')
  })

  it('drops trashed parent_item and sub_item links', () => {
    const schema: PropertySchema = {
      id: 'schema-partner',
      database_id: 'db-1',
      name: 'Sub-items',
      type: 'sub_item',
      config: {},
      position: 0,
      group: 'Standard',
    }
    const value = { related_ids: ['alpha-id', 'trashed-id'] }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveWithTrashed)).toBe('Alpha')
  })

  it('still keeps the raw id when a title is merely unknown', () => {
    const value = { _timeline: { '': { related_ids: ['unknown-id', 'trashed-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveWithTrashed),
    ).toBe('unknown-id')
  })
})

// ── Select labels ─────────────────────────────────────────────────────────────

import { formatSelectValue } from '../cellUtils'

describe('formatSelectValue', () => {
  const options = [{ id: 'o1', label: 'Value A' }, { id: 'o2', label: 'Value B' }]

  it('shows the labels of the stored ids', () => {
    expect(formatSelectValue({ option: 'o2' }, targetSchema('select', { options }))).toBe('Value B')
    expect(
      formatSelectValue({ options: ['o2', 'o1'] }, targetSchema('select', { options, mode: 'multiple' })),
    ).toBe('Value B, Value A')
  })

  it('leaves out ids the property no longer offers', () => {
    expect(
      formatSelectValue({ options: ['gone', 'o1'] }, targetSchema('select', { options, mode: 'multiple' })),
    ).toBe('Value A')
    expect(formatSelectValue({ option: 'gone' }, targetSchema('select', { options }))).toBe('')
  })
})

describe('chipIcon', () => {
  it("shows the entry's own icon", () => {
    expect(chipIcon('mdi:star-outline')).toBe('mdi:star-outline')
  })

  it('falls back to the default icon when there is none or it is not known yet', () => {
    expect(chipIcon(null)).toBe(DEFAULT_ENTRY_ICON)
    expect(chipIcon(undefined)).toBe(DEFAULT_ENTRY_ICON)
    expect(chipIcon('')).toBe(DEFAULT_ENTRY_ICON)
  })
})
