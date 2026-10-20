/**
 * formulaSyntax
 *
 * The formula syntax reference shown in the property settings. The list of
 * functions and operators comes from the backend (``GET
 * /api/databases/formula-syntax``), which reads it from the formula engine's
 * function registry, so the reference always names exactly what evaluation
 * knows. Descriptions and examples are UI text and live in the locales under
 * ``db.formulaHelp.<key>``.
 *
 * The reference is the same for every database and does not change while the
 * app runs, so it is loaded once and cached; a failed load is retried on the
 * next call.
 *
 * Usage:
 *   const syntax = await loadFormulaSyntax()
 *   const groups = groupSyntaxEntries(syntax, filterSyntaxEntries(syntax.entries, query, textOf))
 */
import { apiClient } from '@/api/client'

/** One function or piece of syntax, as the backend lists it. */
export interface FormulaSyntaxEntry {
  /** Locale key of its description and example. */
  key: string
  name: string
  category: string
  signature: string
  /** Text the editor inserts; empty when there is nothing to insert. */
  insert: string
}

export interface FormulaSyntax {
  /** Categories in display order. */
  categories: string[]
  entries: FormulaSyntaxEntry[]
}

/** The entries of one category, for rendering. */
export interface FormulaSyntaxGroup {
  category: string
  entries: FormulaSyntaxEntry[]
}

let cached: FormulaSyntax | null = null
let inFlight: Promise<FormulaSyntax> | null = null

/** The formula syntax reference, loaded once. */
export function loadFormulaSyntax(): Promise<FormulaSyntax> {
  if (cached) return Promise.resolve(cached)
  if (inFlight) return inFlight
  inFlight = apiClient
    .get<FormulaSyntax>('/api/databases/formula-syntax')
    .then(syntax => {
      cached = syntax
      return syntax
    })
    .finally(() => {
      inFlight = null
    })
  return inFlight
}

/** Forget the cached reference (tests). */
export function resetFormulaSyntaxCache(): void {
  cached = null
  inFlight = null
}

/**
 * The entries matching *query*, case-insensitively, in their name, signature
 * or the text *textOf* gives for them (its description and category label in
 * the user's language). All entries for a blank query.
 */
export function filterSyntaxEntries(
  entries: readonly FormulaSyntaxEntry[],
  query: string,
  textOf: (entry: FormulaSyntaxEntry) => string,
): FormulaSyntaxEntry[] {
  const q = query.trim().toLowerCase()
  if (!q) return [...entries]
  return entries.filter(
    e =>
      e.name.toLowerCase().includes(q)
      || e.signature.toLowerCase().includes(q)
      || textOf(e).toLowerCase().includes(q),
  )
}

/** *entries* grouped by the categories of *syntax*, in their order, empty groups left out. */
export function groupSyntaxEntries(
  syntax: FormulaSyntax,
  entries: readonly FormulaSyntaxEntry[],
): FormulaSyntaxGroup[] {
  return syntax.categories
    .map(category => ({ category, entries: entries.filter(e => e.category === category) }))
    .filter(group => group.entries.length > 0)
}
