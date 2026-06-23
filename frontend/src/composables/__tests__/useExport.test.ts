import { describe, it, expect } from 'vitest'
import { buildCsvHeader } from '@/composables/useExport'

// ── buildCsvHeader ────────────────────────────────────────────────────────────

describe('buildCsvHeader', () => {
  it('returns the plain name when the description is empty', () => {
    expect(buildCsvHeader('Status', '', 'Description')).toBe('Status')
  })

  it('returns the plain name when the description is whitespace only', () => {
    expect(buildCsvHeader('Status', '   ', 'Description')).toBe('Status')
  })

  it('appends a bracketed marker when a description is present', () => {
    expect(buildCsvHeader('Status', 'Workflow stage', 'Description')).toBe(
      'Status [Description: Workflow stage]',
    )
  })

  it('trims the description before embedding it', () => {
    expect(buildCsvHeader('Status', '  Workflow stage  ', 'Description')).toBe(
      'Status [Description: Workflow stage]',
    )
  })

  it('uses the supplied prefix label verbatim', () => {
    expect(buildCsvHeader('Status', 'Stage', 'Note')).toBe('Status [Note: Stage]')
  })

  it('tolerates an undefined description (defensive)', () => {
    expect(buildCsvHeader('Status', undefined as unknown as string, 'Description')).toBe('Status')
  })
})
