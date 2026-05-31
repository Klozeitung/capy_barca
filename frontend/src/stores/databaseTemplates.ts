/**
 * databaseTemplates store
 *
 * Thin Pinia store for the entry-template system.
 *
 * Responsibilities
 * ----------------
 * - Fetch and cache the template list for a given database.
 * - Create a new template via POST /{database_id}/entry-templates.
 * - Apply a template to an existing entry via
 *   POST /{database_id}/entry-templates/{tid}/apply/{entry_id}.
 *
 * The store does not manage template block content (that is handled by
 * DatabaseTemplateEditor, which reuses SideView's entry-editing stack).
 * It holds only the lightweight list of template descriptors needed to
 * render the template picker in the database empty state and toolbar.
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface EntryTemplate {
  id: string
  position: number
  content: Record<string, unknown> | null
  icon: string | null
  values: Record<string, unknown | null>
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useDatabaseTemplatesStore = defineStore('databaseTemplates', () => {
  /** Template lists keyed by database block ID. */
  const templates = ref<Record<string, EntryTemplate[]>>({})

  /** Whether a fetch is currently in progress, keyed by database ID. */
  const loading = ref<Record<string, boolean>>({})

  // ── Fetch ─────────────────────────────────────────────────────────────────

  async function fetchTemplates(databaseId: string): Promise<EntryTemplate[]> {
    loading.value[databaseId] = true
    try {
      const result = await apiClient.get<EntryTemplate[]>(
        `/api/databases/${databaseId}/entry-templates`,
      )
      templates.value[databaseId] = result
      return result
    } finally {
      loading.value[databaseId] = false
    }
  }

  function getTemplates(databaseId: string): EntryTemplate[] {
    return templates.value[databaseId] ?? []
  }

  function isLoading(databaseId: string): boolean {
    return loading.value[databaseId] ?? false
  }

  // ── Create ────────────────────────────────────────────────────────────────

  async function createTemplate(databaseId: string): Promise<EntryTemplate> {
    const template = await apiClient.post<EntryTemplate>(
      `/api/databases/${databaseId}/entry-templates`,
      {},
    )
    await fetchTemplates(databaseId)
    return template
  }

  // ── Apply ─────────────────────────────────────────────────────────────────

  async function applyTemplate(
    databaseId: string,
    templateId: string,
    entryId: string,
  ): Promise<void> {
    await apiClient.post<void>(
      `/api/databases/${databaseId}/entry-templates/${templateId}/apply/${entryId}`,
      {},
    )
  }

  // ── Delete (via block store soft-delete, invalidate cache) ────────────────

  function invalidate(databaseId: string): void {
    delete templates.value[databaseId]
  }

  return {
    templates,
    loading,
    fetchTemplates,
    getTemplates,
    isLoading,
    createTemplate,
    applyTemplate,
    invalidate,
  }
})
