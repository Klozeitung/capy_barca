/**
 * calendarColors
 *
 * Shared chip-color palette for CalendarView and CalendarFastEditModal.
 * Colors use solid backgrounds with white text for legibility.
 */

export interface ChipColor {
  key: string
  bg: string    // CSS background value
  text: string  // always '#fff' for contrast
}

export const CHIP_COLORS: ChipColor[] = [
  { key: 'blue',   bg: 'var(--color-accent)',      text: '#fff' },
  { key: 'red',    bg: '#e05555',                  text: '#fff' },
  { key: 'orange', bg: '#e07830',                  text: '#fff' },
  { key: 'yellow', bg: '#c8a010',                  text: '#fff' },
  { key: 'green',  bg: '#30a050',                  text: '#fff' },
  { key: 'teal',   bg: '#1a8a7a',                  text: '#fff' },
  { key: 'purple', bg: '#8040c0',                  text: '#fff' },
  { key: 'pink',   bg: '#d04090',                  text: '#fff' },
  { key: 'gray',   bg: 'rgba(100,100,100,0.85)',   text: '#fff' },
]

export const DEFAULT_CHIP_COLOR = 'blue'

export function getChipColor(key?: string): ChipColor {
  return CHIP_COLORS.find(c => c.key === key) ?? CHIP_COLORS[0]
}

export function chipStyle(key?: string): { background: string; color: string } {
  const c = getChipColor(key)
  return { background: c.bg, color: c.text }
}
