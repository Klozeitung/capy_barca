/**
 * useEditable
 *
 * Whether the current user may change what a component shows.
 *
 * The backend sends ``can_write`` with every block. Permissions narrow
 * downwards (a block grants at most what its ancestors grant), so the value
 * of a block is already the whole answer for that block and everything a
 * component shows of it; no combination with the levels above is needed.
 *
 * Components that stand for a block provide its value with
 * ``provideEditable``: the page views provide the page, a database block
 * provides the database, an editor row provides its block. Everything below
 * reads the nearest value with ``useEditable`` instead of receiving a prop
 * through every layer. Without any provider, everything is editable.
 *
 * A block without ``can_write`` (a field the backend always sends, so this
 * should not happen) passes on the value of the provider above. Hiding an
 * editing control is a convenience: the backend refuses every change a
 * reader attempts, and the read-only notice explains the refusal.
 */
import {
  computed,
  inject,
  provide,
  toValue,
  type ComputedRef,
  type InjectionKey,
  type MaybeRefOrGetter,
} from 'vue'

const EDITABLE_KEY: InjectionKey<ComputedRef<boolean>> = Symbol('editable')

/** The ``can_write`` value a block carries, if any. */
export interface WriteAccess {
  can_write?: boolean
}

/**
 * Whether *block* may be changed by the current user.
 *
 * ``false`` only when the backend said so; a missing block or a missing
 * value counts as writable (see the module documentation).
 */
export function canWrite(block: WriteAccess | null | undefined): boolean {
  return block?.can_write !== false
}

/** The editable state of the nearest provider above the calling component. */
export function useEditable(): ComputedRef<boolean> {
  const parent = inject(EDITABLE_KEY, null)
  return computed(() => (parent ? parent.value : true))
}

/**
 * Provide the editable state of *block* to the calling component's
 * descendants and return it for the component itself.
 *
 * *block* is read reactively. When it has no ``can_write`` value, the state
 * of the provider above is passed on.
 */
export function provideEditable(
  block: MaybeRefOrGetter<WriteAccess | null | undefined>,
): ComputedRef<boolean> {
  const parent = inject(EDITABLE_KEY, null)
  const editable = computed(() => {
    const own = toValue(block)?.can_write
    if (own !== undefined) return own
    return parent ? parent.value : true
  })
  provide(EDITABLE_KEY, editable)
  return editable
}
