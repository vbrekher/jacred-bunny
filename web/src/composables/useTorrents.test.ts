import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { useTorrents } from '@/composables/useTorrents'
import { useShellTools } from '@/composables/useShellTools'
import { apiClient } from '@/lib/api/client'
import { setApiKey } from '@/lib/storage'

vi.mock('vue-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const cleanup: Array<() => void> = []
beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup.splice(0).forEach(fn => fn()); vi.restoreAllMocks() })

async function render() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  await router.push('/')
  const client = new QueryClient()
  // Override the page's automatic retry so each test observes the failed attempt.
  client.setQueryDefaults(['torrents'], { retry: false })
  let state!: ReturnType<typeof useTorrents>
  let shell!: ReturnType<typeof useShellTools>
  const host = defineComponent({ setup() { state = useTorrents(); shell = useShellTools(); return {} }, template: '<div />' })
  const wrapper = mount(host, { global: { plugins: [router, i18n, [VueQueryPlugin, { queryClient: client }]] } })
  cleanup.push(() => { wrapper.unmount(); client.clear(); shell.apiKeyOpen.value = false })
  // Page retry is explicitly 1. Eliminate its delay without replacing the query machinery.
  client.setDefaultOptions({ queries: { retryDelay: 0 } })
  return { state, shell }
}

async function settle() { await flushPromises(); await new Promise(resolve => setTimeout(resolve, 20)); await flushPromises() }

describe('search retry', () => {
  it('runs the same query after the missing API key is saved', async () => {
    const conf = vi.spyOn(apiClient, 'getConf').mockResolvedValue({ apikey: false } as never)
    const get = vi.spyOn(apiClient, 'getTorrents').mockResolvedValue([])
    const { state, shell } = await render()
    state.query.value = 'Battle Spirits'
    await state.search(); await settle()
    await vi.waitFor(() => expect(state.errorMessage.value).not.toBe(''))
    expect(shell.apiKeyOpen.value).toBe(true)
    expect(get).not.toHaveBeenCalled()
    setApiKey('test-app-key')
    conf.mockResolvedValue({ apikey: true } as never)
    shell.notifyApiKeySaved(); await settle()
    expect(get).toHaveBeenCalledTimes(1)
    expect(state.errorMessage.value).toBe('')
  })

  it('refetches an unchanged successful query on explicit retry', async () => {
    vi.spyOn(apiClient, 'getConf').mockResolvedValue({ apikey: true } as never)
    const get = vi.spyOn(apiClient, 'getTorrents').mockResolvedValue([])
    const { state } = await render()
    state.query.value = 'Battle Spirits'
    await state.search(); await settle()
    expect(get).toHaveBeenCalledTimes(1)
    state.retrySearch(); await settle()
    expect(get).toHaveBeenCalledTimes(2)
  })
})
