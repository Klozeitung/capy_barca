import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import {
  aliasPayload,
  isFreeLabel,
  useKinshipNuancesStore,
  type KinshipNuanceGroup,
} from '@/stores/kinshipNuances'
import {
  hasSystemNuances,
  isKinshipDeleteLocked,
  isKinshipTimelineCoupled,
  kinshipFemaleOptionId,
  kinshipMaleOptionId,
  kinshipPreset,
  kinshipRole,
  kinshipRoleForName,
} from '@/stores/propertyTypes'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
  API_UNAUTHORIZED_EVENT: 'capybarca:unauthorized',
}))

import { apiClient } from '@/api/client'

function parents(aliases: KinshipNuanceGroup['aliases'] = []): KinshipNuanceGroup {
  return {
    group: 'parents',
    bases: [{ id: 'kin.parent.biological', label: 'biological' }, { id: 'kin.parent.step', label: 'step' }],
    aliases,
  }
}

function siblings(aliases: KinshipNuanceGroup['aliases'] = []): KinshipNuanceGroup {
  return {
    group: 'siblings',
    bases: [{ id: 'kin.sibling.biological', label: 'biological' }, { id: 'kin.sibling.half', label: 'half' }],
    aliases,
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

describe('aliasPayload', () => {
  it('flattens the groups and leaves the id out of new aliases', () => {
    const payload = aliasPayload([
      parents([{ id: 'a-1', base: 'kin.parent.step', label: ' Label A ' }]),
      siblings([{ id: null, base: 'kin.sibling.half', label: 'Label B' }]),
    ])
    expect(payload).toEqual({
      aliases: [
        { id: 'a-1', group: 'parents', base: 'kin.parent.step', label: 'Label A' },
        { group: 'siblings', base: 'kin.sibling.half', label: 'Label B' },
      ],
    })
  })
})

describe('isFreeLabel', () => {
  it('refuses empty labels and labels of base nuances or aliases, ignoring case', () => {
    const group = parents([{ id: 'a-1', base: 'kin.parent.step', label: 'Label A' }])
    expect(isFreeLabel(group, '  ')).toBe(false)
    expect(isFreeLabel(group, 'Biological')).toBe(false)
    expect(isFreeLabel(group, 'label a ')).toBe(false)
    expect(isFreeLabel(group, 'Label C')).toBe(true)
  })
})

// ── Store ─────────────────────────────────────────────────────────────────────

describe('useKinshipNuancesStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads the groups', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ groups: [parents(), siblings()] })
    const store = useKinshipNuancesStore()
    await store.load()
    expect(apiClient.get).toHaveBeenCalledWith('/api/settings/kinship-nuances')
    expect(store.groups.map((g) => g.group)).toEqual(['parents', 'siblings'])
    expect(store.loaded).toBe(true)
  })

  it('saves every alias and adopts what the server stored', async () => {
    const stored = [parents([{ id: 'a-9', base: 'kin.parent.step', label: 'Label A' }])]
    vi.mocked(apiClient.put).mockResolvedValueOnce({ groups: stored })
    const store = useKinshipNuancesStore()
    await store.save([parents([{ id: null, base: 'kin.parent.step', label: 'Label A' }])])
    expect(apiClient.put).toHaveBeenCalledWith('/api/settings/kinship-nuances', {
      aliases: [{ group: 'parents', base: 'kin.parent.step', label: 'Label A' }],
    })
    expect(store.groups).toEqual(stored)
  })

  it('asks how many entries use an alias', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ entries: 3 })
    const store = useKinshipNuancesStore()
    expect(await store.usage('a-1')).toBe(3)
    expect(apiClient.get).toHaveBeenCalledWith('/api/settings/kinship-nuances/a-1/usage')
  })
})

// ── Family set roles ──────────────────────────────────────────────────────────

describe('family set roles', () => {
  it('reads the role and the female and male options the server set', () => {
    const sex = { type: 'select', config: { kinRole: 'sex', kinFemaleOptionId: 'o-1', kinMaleOptionId: 'o-2' } }
    expect(kinshipRole(sex)).toBe('sex')
    expect(kinshipFemaleOptionId(sex)).toBe('o-1')
    expect(kinshipMaleOptionId(sex)).toBe('o-2')
    expect(kinshipMaleOptionId({ type: 'select', config: { kinMaleOptionId: 'o-2' } })).toBeNull()
    expect(kinshipRole({ type: 'text', config: { kinRole: 'other' } })).toBeNull()
    expect(kinshipFemaleOptionId({ type: 'select', config: { kinFemaleOptionId: 'o-1' } })).toBeNull()
  })

  it('gives system nuances to the relations of the set only', () => {
    expect(hasSystemNuances({ type: 'relation', config: { kinRole: 'partners' } })).toBe(true)
    expect(hasSystemNuances({ type: 'relation', config: {} })).toBe(false)
    expect(hasSystemNuances({ type: 'date', config: { kinRole: 'birth' } })).toBe(false)
  })

  it('couples the timelines of parents, children and siblings', () => {
    expect(isKinshipTimelineCoupled({ type: 'relation', config: { kinRole: 'siblings' } })).toBe(true)
    expect(isKinshipTimelineCoupled({ type: 'relation', config: { kinRole: 'children' } })).toBe(true)
    expect(isKinshipTimelineCoupled({ type: 'relation', config: { kinRole: 'partners' } })).toBe(false)
  })

  it('locks deleting only while a family tree view exists', () => {
    const birth = { type: 'date', config: { kinRole: 'birth' } }
    expect(isKinshipDeleteLocked(birth, ['table', 'family_tree'])).toBe(true)
    expect(isKinshipDeleteLocked(birth, ['table'])).toBe(false)
    expect(isKinshipDeleteLocked({ type: 'date', config: {} }, ['family_tree'])).toBe(false)
  })

  it('recognises reserved names in either language, ignoring case and spaces', () => {
    expect(kinshipRoleForName(' eltern ')).toEqual({ role: 'parents', language: 'de' })
    expect(kinshipRoleForName('SIBLINGS')).toEqual({ role: 'siblings', language: 'en' })
    expect(kinshipRoleForName('Halbgeschwister')).toBeNull()
    expect(kinshipRoleForName('Parent')).toBeNull()
    expect(kinshipRoleForName('')).toBeNull()
  })

  it('presets the structure a reserved name needs', () => {
    expect(kinshipPreset('children', 'de')).toEqual({
      types: ['relation'], relation: { direction: 'bilateral', mirrorName: 'Eltern' },
    })
    expect(kinshipPreset('siblings', 'en').relation?.direction).toBe('bilateral_self')
    expect(kinshipPreset('death', 'en').types).toEqual(['date', 'rollup', 'formula'])
    expect(kinshipPreset('sex', 'de').types).toEqual(['select'])
  })
})
