/**
 * blockColors
 *
 * Shared color palette for block text-color (#26) and background-color (#26).
 *
 * Stored in block.content.color (text) and block.content.bgColor (background)
 * as the key strings defined here. The CSS value is derived at render time
 * so no migration is needed if the palette changes.
 *
 * Colors are tuned to look reasonable in both light and dark themes.
 */

export const BLOCK_COLORS: Record<string, string | null> = {
  default: null,
  gray:    '#9b9b9b',
  brown:   '#b5846a',
  orange:  '#e0841a',
  yellow:  '#d4a017',
  green:   '#4aac6d',
  blue:    '#3d9ec0',
  purple:  '#9d6dc4',
  pink:    '#cf5ca0',
  red:     '#d95555',
}

export const BLOCK_BG_COLORS: Record<string, string | null> = {
  default:   null,
  gray_bg:   'rgba(155,155,155,0.15)',
  brown_bg:  'rgba(181,132,106,0.15)',
  orange_bg: 'rgba(224,132,26,0.15)',
  yellow_bg: 'rgba(212,160,23,0.15)',
  green_bg:  'rgba(74,172,109,0.15)',
  blue_bg:   'rgba(61,158,192,0.15)',
  purple_bg: 'rgba(157,109,196,0.15)',
  pink_bg:   'rgba(207,92,160,0.15)',
  red_bg:    'rgba(217,85,85,0.15)',
}

/** Resolve a stored color key to its CSS value, or null for default. */
export function resolveColor(key: string | undefined | null): string | null {
  if (!key || key === 'default') return null
  return BLOCK_COLORS[key] ?? null
}

export function resolveBgColor(key: string | undefined | null): string | null {
  if (!key || key === 'default') return null
  return BLOCK_BG_COLORS[key] ?? null
}
