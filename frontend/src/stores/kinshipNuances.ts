import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'

const URL = '/api/settings/kinship-nuances'

export type KinshipGroup = 'parents' | 'partners' | 'siblings' | 'half_siblings'

/** A fixed nuance of the family set, labelled in the account's language. */
export interface KinshipBaseNuance {
  id: string
  label: string
}

/** A nuance an administrator added. ``base`` is null for the sibling groups. */
export interface KinshipAlias {
  id: string | null
  base: string | null
  label: string
}

export interface KinshipNuanceGroup {
  group: KinshipGroup
  bases: KinshipBaseNuance[]
  aliases: KinshipAlias[]
}

interface KinshipNuancesResponse {
  groups: KinshipNuanceGroup[]
}

/**
 * The aliases of every group in the form ``PUT`` expects. An alias without
 * id is new; the server assigns one.
 */
export function aliasPayload(groups: KinshipNuanceGroup[]) {
  return {
    aliases: groups.flatMap((g) =>
      g.aliases.map((a) => ({
        ...(a.id ? { id: a.id } : {}),
        group: g.group,
        base: a.base,
        label: a.label.trim(),
      })),
    ),
  }
}

/**
 * Whether *label* can be added to *group*: not empty, and neither a base
 * nuance nor an alias of the group carries it already (ignoring case).
 */
export function isFreeLabel(group: KinshipNuanceGroup, label: string): boolean {
  const folded = label.trim().toLocaleLowerCase()
  if (!folded) return false
  return ![...group.bases, ...group.aliases].some(
    (n) => n.label.trim().toLocaleLowerCase() === folded,
  )
}

/**
 * The system nuances of the family set and the aliases administrators add
 * to them (settings, "System defaults"). Only administrators may read and
 * change them; the server refuses everyone else.
 *
 * An alias counts as its base nuance wherever the system interprets
 * nuances. Deleting one moves every value that used it to its base nuance,
 * or removes it in the sibling groups, which have none; ``usage`` tells the
 * confirmation how many entries that affects.
 */
export const useKinshipNuancesStore = defineStore('kinshipNuances', () => {
  const groups = ref<KinshipNuanceGroup[]>([])
  const loaded = ref(false)

  async function load(): Promise<void> {
    const result = await apiClient.get<KinshipNuancesResponse>(URL)
    groups.value = result.groups
    loaded.value = true
  }

  /** Store *next* as the aliases of every group and adopt what the server kept. */
  async function save(next: KinshipNuanceGroup[]): Promise<void> {
    const result = await apiClient.put<KinshipNuancesResponse>(URL, aliasPayload(next))
    groups.value = result.groups
    loaded.value = true
  }

  /** Active entries using the alias *aliasId*, across all databases. */
  async function usage(aliasId: string): Promise<number> {
    const result = await apiClient.get<{ entries: number }>(
      `${URL}/${encodeURIComponent(aliasId)}/usage`,
    )
    return result.entries
  }

  return { groups, loaded, load, save, usage }
})
