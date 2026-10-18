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
 */
import { addCollection, type IconifyJSON } from '@iconify/vue'
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
