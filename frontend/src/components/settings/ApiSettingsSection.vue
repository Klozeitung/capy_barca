<script setup lang="ts">
/**
 * ApiSettingsSection
 *
 * Content of the "API" tab of the global settings (admin only): the
 * connection to a local AI service and the custom printer used by Quick
 * Print. Both are installation-wide and stored by the backend
 * (``/api/settings/api``).
 *
 * Secrets (the AI key, header argument values) are never sent back by the
 * backend; the form only learns whether one is set. An input left empty keeps
 * the stored secret, which is why the request sends ``null`` for it.
 *
 * "Check connection" saves first and then lets the backend send a short test
 * text to the stored printer; only a 200 answer shows as success.
 */
import { ref, computed, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { apiClient, ApiError } from '@/api/client'

const { t } = useI18n()

// ── Types ─────────────────────────────────────────────────────────────────────

type ArgumentTarget = 'body' | 'query' | 'header'

interface ArgumentOut {
  name: string
  target: ArgumentTarget
  value: string | null
  value_set: boolean
}

interface ApiSettingsOut {
  local_ai: { host: string; key_set: boolean }
  custom_printer: { url: string; text_field: string; arguments: ArgumentOut[] }
}

interface PrintResult {
  ok: boolean
  status: number | null
  body: string
}

interface ArgumentRow {
  /** Stable key for the list, independent of the editable name. */
  uid: number
  name: string
  target: ArgumentTarget
  value: string
  /** A header value is stored; an empty input keeps it. */
  valueSet: boolean
}

const TARGETS: { value: ArgumentTarget; labelKey: string }[] = [
  { value: 'body', labelKey: 'settings.apiArgTargetBody' },
  { value: 'query', labelKey: 'settings.apiArgTargetQuery' },
  { value: 'header', labelKey: 'settings.apiArgTargetHeader' },
]

// ── State ─────────────────────────────────────────────────────────────────────

const loading = ref(true)
const loadError = ref(false)

const aiHost = ref('')
const aiKey = ref('')
const aiKeySet = ref(false)
const aiKeyClear = ref(false)

const printerUrl = ref('')
const printerTextField = ref('text')
const argumentRows = ref<ArgumentRow[]>([])
let _nextUid = 1

const saving = ref(false)
const feedback = ref<{ msg: string; ok: boolean } | null>(null)

const testing = ref(false)
const testResult = ref<PrintResult | null>(null)

const busy = computed(() => saving.value || testing.value)

// ── Conversion ────────────────────────────────────────────────────────────────

function applySettings(data: ApiSettingsOut): void {
  aiHost.value = data.local_ai.host
  aiKey.value = ''
  aiKeySet.value = data.local_ai.key_set
  aiKeyClear.value = false
  printerUrl.value = data.custom_printer.url
  printerTextField.value = data.custom_printer.text_field
  argumentRows.value = data.custom_printer.arguments.map((a) => ({
    uid: _nextUid++,
    name: a.name,
    target: a.target,
    value: a.target === 'header' ? '' : (a.value ?? ''),
    valueSet: a.target === 'header' && a.value_set,
  }))
}

function payload(): Record<string, unknown> {
  let key: string | null = null
  if (aiKeyClear.value) key = ''
  else if (aiKey.value) key = aiKey.value
  return {
    local_ai: { host: aiHost.value.trim(), key },
    custom_printer: {
      url: printerUrl.value.trim(),
      text_field: printerTextField.value.trim() || 'text',
      arguments: argumentRows.value
        .filter((row) => row.name.trim())
        .map((row) => ({
          name: row.name.trim(),
          target: row.target,
          // An empty header input keeps the stored value.
          value: row.target === 'header' ? (row.value || null) : row.value,
        })),
    },
  }
}

// ── Actions ───────────────────────────────────────────────────────────────────

async function load(): Promise<void> {
  loading.value = true
  loadError.value = false
  try {
    applySettings(await apiClient.get<ApiSettingsOut>('/api/settings/api'))
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function save(): Promise<boolean> {
  saving.value = true
  feedback.value = null
  try {
    applySettings(await apiClient.put<ApiSettingsOut>('/api/settings/api', payload()))
    feedback.value = { msg: t('settings.apiSaved'), ok: true }
    return true
  } catch (e) {
    const msg = e instanceof ApiError && e.status === 422
      ? t('settings.apiInvalid')
      : t('settings.apiSaveError')
    feedback.value = { msg, ok: false }
    return false
  } finally {
    saving.value = false
  }
}

async function checkConnection(): Promise<void> {
  testResult.value = null
  if (!printerUrl.value.trim()) {
    feedback.value = { msg: t('settings.apiTestNoPrinter'), ok: false }
    return
  }
  if (!(await save())) return
  testing.value = true
  try {
    testResult.value = await apiClient.post<PrintResult>('/api/settings/api/printer-test')
  } catch (e) {
    testResult.value = {
      ok: false,
      status: e instanceof ApiError ? e.status : null,
      body: e instanceof Error ? e.message : String(e),
    }
  } finally {
    testing.value = false
  }
}

function addArgument(): void {
  argumentRows.value.push({ uid: _nextUid++, name: '', target: 'body', value: '', valueSet: false })
}

function removeArgument(uid: number): void {
  argumentRows.value = argumentRows.value.filter((row) => row.uid !== uid)
}

function onTargetChange(row: ArgumentRow): void {
  // A value typed for another target must not silently become a header
  // secret, nor a stored secret be reported for a non-header.
  row.valueSet = false
}

onMounted(load)
</script>

<template>
  <div class="api">
    <p class="api__desc">{{ t('settings.apiDesc') }}</p>

    <div v-if="loading" class="api__loading">
      <Icon icon="mdi:loading" width="18" height="18" class="api__spin" />
    </div>

    <div v-else-if="loadError" class="api__error">
      <Icon icon="mdi:alert-circle-outline" width="16" height="16" />
      {{ t('settings.apiLoadError') }}
    </div>

    <template v-else>
      <!-- ── Custom printer ─────────────────────────────────────────────── -->
      <section class="api__card">
        <h3 class="api__card-title">
          <Icon icon="mdi:printer-outline" width="15" height="15" />
          {{ t('settings.apiPrinter') }}
        </h3>
        <p class="api__hint">{{ t('settings.apiPrinterDesc') }}</p>

        <label class="api__field">
          <span class="api__label">{{ t('settings.apiPrinterUrl') }}</span>
          <input
            v-model="printerUrl"
            class="api__input"
            type="url"
            :placeholder="t('settings.apiPrinterUrlPlaceholder')"
            autocomplete="off"
            spellcheck="false"
          />
          <span class="api__hint">{{ t('settings.apiPrinterUrlHint') }}</span>
        </label>

        <label class="api__field">
          <span class="api__label">{{ t('settings.apiPrinterTextField') }}</span>
          <input
            v-model="printerTextField"
            class="api__input api__input--short"
            type="text"
            autocomplete="off"
            spellcheck="false"
          />
          <span class="api__hint">{{ t('settings.apiPrinterTextFieldHint') }}</span>
        </label>

        <div class="api__field">
          <span class="api__label">{{ t('settings.apiPrinterArguments') }}</span>
          <p v-if="argumentRows.length === 0" class="api__hint">{{ t('settings.apiArgsEmpty') }}</p>
          <div v-for="row in argumentRows" :key="row.uid" class="api__arg">
            <input
              v-model="row.name"
              class="api__input api__arg-name"
              type="text"
              :placeholder="t('settings.apiArgName')"
              :aria-label="t('settings.apiArgName')"
              autocomplete="off"
              spellcheck="false"
            />
            <select
              v-model="row.target"
              class="api__input api__arg-target"
              :aria-label="t('settings.apiArgTarget')"
              @change="onTargetChange(row)"
            >
              <option v-for="target in TARGETS" :key="target.value" :value="target.value">
                {{ t(target.labelKey) }}
              </option>
            </select>
            <input
              v-model="row.value"
              class="api__input api__arg-value"
              :type="row.target === 'header' ? 'password' : 'text'"
              :placeholder="row.target === 'header' && row.valueSet ? t('settings.apiSecretSet') : t('settings.apiArgValue')"
              :aria-label="t('settings.apiArgValue')"
              autocomplete="new-password"
              spellcheck="false"
            />
            <button
              class="api__icon-btn"
              :title="t('settings.apiArgRemove')"
              :aria-label="t('settings.apiArgRemove')"
              @click="removeArgument(row.uid)"
            >
              <Icon icon="mdi:close" width="14" height="14" />
            </button>
          </div>
          <button class="api__add" @click="addArgument">
            <Icon icon="mdi:plus" width="14" height="14" />
            {{ t('settings.apiArgAdd') }}
          </button>
          <span class="api__hint">{{ t('settings.apiPrinterArgumentsHint') }}</span>
        </div>

        <div class="api__test">
          <button class="api__btn" :disabled="busy" :title="t('settings.apiTestHint')" @click="checkConnection">
            <Icon
              :icon="testing ? 'mdi:loading' : 'mdi:lan-connect'"
              width="14"
              height="14"
              :class="{ api__spin: testing }"
            />
            {{ testing ? t('settings.apiTesting') : t('settings.apiTest') }}
          </button>
          <div
            v-if="testResult"
            class="api__result"
            :class="testResult.ok ? 'api__result--ok' : 'api__result--err'"
            role="status"
          >
            <span class="api__result-head">
              <Icon :icon="testResult.ok ? 'mdi:check-circle-outline' : 'mdi:alert-circle-outline'" width="14" height="14" />
              {{ testResult.status === null
                ? t('settings.apiTestNoAnswer', { reason: testResult.body })
                : t('settings.apiTestStatus', { status: testResult.status }) }}
            </span>
            <pre v-if="testResult.status !== null && testResult.body" class="api__result-body">{{ testResult.body }}</pre>
          </div>
        </div>
      </section>

      <!-- ── Local AI ───────────────────────────────────────────────────── -->
      <section class="api__card">
        <h3 class="api__card-title">
          <Icon icon="mdi:robot-outline" width="15" height="15" />
          {{ t('settings.apiLocalAi') }}
        </h3>
        <p class="api__hint">{{ t('settings.apiLocalAiDesc') }}</p>

        <label class="api__field">
          <span class="api__label">{{ t('settings.apiLocalAiHost') }}</span>
          <input
            v-model="aiHost"
            class="api__input"
            type="text"
            :placeholder="t('settings.apiLocalAiHostPlaceholder')"
            autocomplete="off"
            spellcheck="false"
          />
        </label>

        <label class="api__field">
          <span class="api__label">{{ t('settings.apiLocalAiKey') }}</span>
          <input
            v-model="aiKey"
            class="api__input"
            type="password"
            :placeholder="aiKeySet && !aiKeyClear ? t('settings.apiSecretSet') : ''"
            :disabled="aiKeyClear"
            autocomplete="new-password"
          />
        </label>
        <label v-if="aiKeySet" class="api__check">
          <input v-model="aiKeyClear" type="checkbox" />
          {{ t('settings.apiSecretClear') }}
        </label>
      </section>

      <div class="api__footer">
        <p
          v-if="feedback"
          class="api__feedback"
          :class="feedback.ok ? 'api__feedback--ok' : 'api__feedback--err'"
        >
          {{ feedback.msg }}
        </p>
        <button class="api__btn api__btn--primary" :disabled="busy" @click="save">
          {{ saving ? t('settings.apiSaving') : t('settings.apiSave') }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.api {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.api__desc {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  line-height: 1.5;
}

.api__loading {
  display: flex;
  justify-content: center;
  padding: 2rem;
  color: var(--color-text-muted);
}

.api__error {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8125rem;
  color: #e05353;
}

/* ── Card ────────────────────────────────────────────────────────────────── */
.api__card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg);
  padding: 1rem 1.125rem;
}

.api__card-title {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text);
}

/* ── Fields ──────────────────────────────────────────────────────────────── */
.api__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.api__label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.api__hint {
  margin: 0;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  line-height: 1.45;
}

.api__input {
  padding: 6px 8px;
  font-size: 0.8125rem;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-surface);
  color: var(--color-text);
  min-width: 0;
}

.api__input:focus {
  outline: none;
  border-color: var(--color-accent);
}

.api__input:disabled {
  opacity: 0.5;
}

.api__input--short {
  max-width: 220px;
}

.api__check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}

/* ── Arguments ───────────────────────────────────────────────────────────── */
.api__arg {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 96px minmax(0, 1.4fr) 28px;
  gap: 6px;
  align-items: center;
}

.api__icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: none;
  color: var(--color-text-muted);
  cursor: pointer;
}

.api__icon-btn:hover {
  border-color: #e05353;
  color: #e05353;
}

.api__add {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  border: 1px dashed var(--color-border);
  border-radius: 6px;
  background: none;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  cursor: pointer;
}

.api__add:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
.api__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  font-size: 0.8125rem;
  font-family: inherit;
  font-weight: 500;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: none;
  color: var(--color-text);
  cursor: pointer;
  white-space: nowrap;
}

.api__btn:hover:not(:disabled) {
  background: var(--color-hover);
}

.api__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.api__btn--primary {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: #fff;
}

.api__btn--primary:hover:not(:disabled) {
  background: var(--color-accent);
  opacity: 0.88;
}

/* ── Test result ─────────────────────────────────────────────────────────── */
.api__test {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.api__result {
  width: 100%;
  border: 1px solid;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.8125rem;
}

.api__result--ok {
  border-color: #4caf7d;
  color: #4caf7d;
  background: color-mix(in srgb, #4caf7d 8%, transparent);
}

.api__result--err {
  border-color: #e05353;
  color: #e05353;
  background: color-mix(in srgb, #e05353 8%, transparent);
}

.api__result-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
}

.api__result-body {
  margin: 6px 0 0;
  max-height: 140px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, monospace;
  font-size: 0.75rem;
  color: var(--color-text);
}

/* ── Footer ──────────────────────────────────────────────────────────────── */
.api__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}

.api__feedback {
  margin: 0;
  font-size: 0.8125rem;
}

.api__feedback--ok  { color: #4caf7d; }
.api__feedback--err { color: #e05353; }

.api__spin {
  animation: api-spin 1s linear infinite;
}

@keyframes api-spin {
  to { transform: rotate(360deg); }
}
</style>
