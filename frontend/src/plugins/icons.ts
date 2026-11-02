/**
 * Offline icon data.
 *
 * Iconify's <Icon> component fetches the data of every icon it has not seen
 * from a public API at runtime. The three icon sets the application offers are
 * already part of the bundle (the icon picker lists them), so registering them
 * up front makes every icon render from local data: no request leaves the
 * installation, icons work without internet access, and the Content Security
 * Policy can keep connect-src at 'self'.
 *
 * Call ``registerIconSets`` once before the application mounts.
 *
 * Uploaded icons
 * --------------
 * Icons an admin uploaded are a set of their own, ``custom``, named
 * ``custom:<id>``. Each is registered as an SVG that draws the uploaded
 * image, so every place that renders an icon through <Icon> shows them as
 * well. Their data comes from the custom icons store: ``setCustomIconSource``
 * hands Iconify a loader for the set, which Iconify asks for any custom icon
 * it has not been given yet, so an icon rendered before the list arrived
 * still appears once it is there.
 */
import {
  addCollection,
  addIcon,
  setCustomIconLoader,
  type IconifyIcon,
  type IconifyJSON,
} from '@iconify/vue'
import mdiData from '@iconify-json/mdi/icons.json'
import tablerData from '@iconify-json/tabler/icons.json'
import lucideData from '@iconify-json/lucide/icons.json'

/** The icon sets available in the application, by Iconify prefix. */
export const ICON_SETS: readonly IconifyJSON[] = [
  mdiData as IconifyJSON,
  tablerData as IconifyJSON,
  lucideData as IconifyJSON,
]

/**
 * Register every bundled icon set with Iconify's local storage.
 *
 * Returns the prefixes that were registered. A set Iconify refuses (malformed
 * data) is left out, so its icons fall back to the remote lookup instead of
 * failing silently at startup.
 */
export function registerIconSets(): string[] {
  return ICON_SETS.filter((set) => addCollection(set)).map((set) => set.prefix)
}

/** Iconify prefix of the uploaded icons (``custom:<id>``). */
export const CUSTOM_ICON_PREFIX = 'custom'

/** Edge of the square uploaded icons are drawn in, in SVG units. */
const CUSTOM_ICON_SIZE = 24

/** True when *icon* names an uploaded icon. */
export function isCustomIcon(icon: string | null | undefined): boolean {
  return !!icon && icon.startsWith(`${CUSTOM_ICON_PREFIX}:`)
}

function escapeAttribute(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/**
 * Iconify data that draws the image at *url*, fitted into the icon's square
 * without distortion.
 */
export function customIconData(url: string): IconifyIcon {
  const size = CUSTOM_ICON_SIZE
  return {
    body:
      `<image href="${escapeAttribute(url)}" width="${size}" height="${size}" ` +
      'preserveAspectRatio="xMidYMid meet"/>',
    width: size,
    height: size,
  }
}

/** Register the uploaded icon *icon* (``custom:<id>``) to draw the image at *url*. */
export function registerCustomIcon(icon: string, url: string): boolean {
  return addIcon(icon, customIconData(url))
}

/**
 * Let Iconify ask *source* for a custom icon it has no data for. *source*
 * receives the icon's name without the prefix and returns the image's URL,
 * or null when no such icon exists.
 */
export function setCustomIconSource(
  source: (name: string) => Promise<string | null>,
): void {
  setCustomIconLoader(async (name) => {
    const url = await source(name)
    return url ? customIconData(url) : null
  }, CUSTOM_ICON_PREFIX)
}
